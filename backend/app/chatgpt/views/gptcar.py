from django.db.models import Q
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from app.chatgpt.models import ChatgptCar
from app.chatgpt.models import ChatgptAccount
from app.chatgpt.serializers import ShowGptCarSerializer, AddChatgptCarModelSerializer, DeleteChatgptCarSerializer
from app.billing.models import supports_commercial_pool_account
from app.page import DefaultPageNumberPagination


def commercial_pool_ids():
    return set(ChatgptCar.objects.filter(is_commercial=True).values_list("id", flat=True))


class GptCarEnum(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        result = ChatgptCar.objects.exclude(id__in=commercial_pool_ids()).order_by("-id").values("id", "car_name")
        return Response({"data": result})


class GptCarView(generics.ListCreateAPIView):
    permission_classes = (IsAuthenticated, IsAdminUser)
    queryset = ChatgptCar.objects.order_by("-id").all()
    serializer_class = ShowGptCarSerializer
    pagination_class = DefaultPageNumberPagination

    def get_queryset(self):
        return super().get_queryset().exclude(id__in=commercial_pool_ids())

    def post(self, request, *args, **kwargs):
        obj = ChatgptCar.objects.filter(id=request.data.get("id")).first()
        protected_pool_ids = commercial_pool_ids()
        if obj and obj.id in protected_pool_ids:
            raise ValidationError({"message": "商业号池请在“商业号池”页面维护，避免套餐账号混乱"})
        requested_account_ids = request.data.get("gpt_account_list") or []
        commercial_accounts = [
            account.chatgpt_username
            for account in ChatgptAccount.objects.filter(id__in=requested_account_ids, is_archived=False)
            if supports_commercial_pool_account(account)
        ]
        if commercial_accounts:
            raise ValidationError({"message": "Plus、Pro、Team、Business 账号只能在“商业号池”页面维护"})
        serializer = AddChatgptCarModelSerializer(instance=obj, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, *args, **kwargs):
        serializer = DeleteChatgptCarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if commercial_pool_ids().intersection(serializer.data["ids"]):
            raise ValidationError({"message": "商业号池不能在旧账号池页面删除"})
        ChatgptCar.objects.filter(id__in=serializer.data["ids"]).delete()
        return Response({"message": "删除成功"})

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.exceptions import ValidationError

from app.settings import (
    ALLOW_REGISTER,
    BILLING_ENABLED,
    EMAIL_VERIFICATION_ENABLED,
    LOCAL_CAPTCHA_ENABLED,
    SHOW_GITHUB,
    TURNSTILE_ENABLED,
    TURNSTILE_SITE_KEY,
)
from app.utils import req_gateway


class VersionConfig(APIView):
    authentication_classes = ()
    permission_classes = ()
    throttle_classes = ()

    def get(self, request):
        return Response({
            'show_github': SHOW_GITHUB,
            'allow_register': ALLOW_REGISTER,
            'billing_enabled': BILLING_ENABLED,
            'email_verification_enabled': EMAIL_VERIFICATION_ENABLED,
            'turnstile_enabled': TURNSTILE_ENABLED,
            'turnstile_site_key': TURNSTILE_SITE_KEY if TURNSTILE_ENABLED else '',
            'local_captcha_enabled': LOCAL_CAPTCHA_ENABLED,
        })


class AccessControlView(APIView):
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request):
        res = req_gateway("get", "/api/blocked-paths")
        return Response(res)

    def post(self, request):
        paths = request.data.get("paths", request.data.get("hash_paths", []))
        res = req_gateway("post", "/api/blocked-paths", json={"paths": paths})
        return Response(res)

import os
import sys

import django

cur_path = os.path.abspath(__file__)
parent = os.path.dirname
sys.path.append(parent(parent(cur_path)))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
django.setup()


def ensure_initial_users(*, admin_username=None, admin_password=None, free_username=None):
    from app.accounts.models import User
    from app.settings import FREE_ACCOUNT_USERNAME, ADMIN_USERNAME, ADMIN_PASSWORD
    from django.contrib.auth.password_validation import validate_password

    admin_username = admin_username or ADMIN_USERNAME
    admin_password = admin_password or ADMIN_PASSWORD
    free_username = free_username or FREE_ACCOUNT_USERNAME

    if not admin_username:
        raise Exception("未设置 超级管理员账密")
    if not admin_password:
        raise Exception("ADMIN_PASSWORD 未设置，请设置后再初始化")


    defaults = {"remark": "超级管理员", "isolated_session": False}
    user, created = User.objects.get_or_create(username=admin_username, defaults=defaults)
    if created or not user.has_usable_password():
        validate_password(admin_password, user)
        user.set_password(admin_password)
    user.is_staff = True
    user.is_active = True
    user.is_superuser = True

    user.save()
    print("Superuser created." if created else "Superuser verified without changing its password.")

    defaults = {
        "remark": "用于免费体验",
        "is_active": False,
        "isolated_session": True,
        "model_limit": [
            {"every_minute": 1, "limit_count": 3, "model_name": "gpt-4"},
            {"every_minute": 1, "limit_count": 3, "model_name": "gpt-4o"},
            {"every_minute": 1, "limit_count": 3, "model_name": "gpt-4o-mini"},
            {"every_minute": 1, "limit_count": 1, "model_name": "o1-mini"},
            {"every_minute": 1, "limit_count": 1, "model_name": "o1", },
            {"every_minute": 1, "limit_count": 1, "model_name": "o1-pro", }

        ]
    }
    user, created = User.objects.get_or_create(username=free_username, defaults=defaults)
    user.is_superuser = False
    user.save()
    print("Freeuser created.")


if __name__ == "__main__":
    ensure_initial_users()

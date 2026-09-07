"""自助註冊的輸入驗證與帳號建立。

與 api.serializers.AccountCreateSerializer（研究者代開帳號）平行存在，刻意
不共用：兩者的必填欄位、同意紀錄、is_research_subject 預設值都不同，硬要
抽共同基底只會讓兩邊的差異變得難讀。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as dj_validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .consent import CONSENT_VERSION

User = get_user_model()


class RegistrationSerializer(serializers.Serializer):
    username = serializers.CharField(
        max_length=150, validators=[UnicodeUsernameValidator()]
    )
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)
    display_name = serializers.CharField(
        max_length=50, required=False, allow_blank=True, default=""
    )
    consent = serializers.BooleanField()

    # consent_version 與 is_research_subject 刻意不是輸入欄位——它們由 create()
    # 決定。讓 client 宣稱自己同意了哪一版、或自己是不是受試者，等於讓受試者
    # 填寫自己的實驗紀錄。

    def validate_username(self, value):
        # iexact 而非精確比對：Django 的登入是大小寫敏感的，允許 Alice 與 alice
        # 並存會讓使用者穩定產生「我明明註冊過卻登不進去」的困惑。
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("這個帳號名稱已經有人用了。")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("這個 email 已經註冊過了。")
        return value

    def validate_consent(self, value):
        if not value:
            raise serializers.ValidationError("必須閱讀並同意研究說明才能註冊。")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "兩次輸入的密碼不一致。"}
            )

        # 傳入未存檔的 User，UserAttributeSimilarityValidator 才有東西可比對。
        # username 與 email 都要帶：該 validator 預設比對 username、first_name、
        # last_name、email 四個屬性，少了 email 就擋不住「密碼跟自己的信箱很像」。
        try:
            dj_validate_password(
                attrs["password"],
                user=User(username=attrs["username"], email=attrs["email"]),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        try:
            # savepoint：Postgres 在 IntegrityError 之後會讓當前交易進入 aborted
            # 狀態，沒有 atomic 包住的話，後續查詢會拋 TransactionManagementError
            # 而不是我們想回的 400。
            with transaction.atomic():
                user = User.objects.create_user(
                    username=validated_data["username"],
                    email=validated_data["email"],
                    password=validated_data["password"],
                )
                user.display_name = validated_data.get("display_name", "")
                user.consent_version = CONSENT_VERSION
                user.is_research_subject = True
                user.save(
                    update_fields=[
                        "display_name",
                        "consent_version",
                        "is_research_subject",
                    ]
                )
        except IntegrityError:
            # validate_username / validate_email 的 exists() 與這裡之間有空窗。
            # 併發註冊撞上時要回 400，不能讓 IntegrityError 冒成 500。
            # 兩個唯一約束都可能命中，訊息寫得涵蓋兩者。
            raise serializers.ValidationError(
                {"username": ["這個帳號名稱或 email 已經有人用了，請再試一次。"]}
            )
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    """忘記密碼第一步：輸入信箱、請系統寄驗證碼。

    只驗格式，不在這裡查帳號存不存在——view 一律回同一句話（不論信箱有沒有
    註冊），避免這個端點變成帳號列舉工具。
    """

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """忘記密碼第二步：驗證碼 + 新密碼。

    刻意不驗 old_password——使用者就是因為不知道舊密碼才走這條路。查帳號、
    比對驗證碼都是 view 的事：view 找得到 target user 才能把它傳進
    dj_validate_password（UserAttributeSimilarityValidator 需要 user 才會
    比對「密碼跟帳號名稱/信箱太像」）。target 為 None（信箱查無此人）時
    其餘 validator 照常生效，弱密碼一樣擋得下來。
    """

    email = serializers.EmailField()
    code = serializers.RegexField(r"^\d{6}$")
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "兩次輸入的新密碼不一致。"}
            )
        try:
            dj_validate_password(
                attrs["new_password"], user=self.context.get("target")
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        return attrs

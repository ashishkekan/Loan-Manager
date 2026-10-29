import hashlib
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.db import transaction
from django.dispatch import receiver
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django import forms
from loans.models import SecuritySetting
from .models import Challenge, Identity, LoginAttempt, LoginSession


class CodeForm(forms.Form):
    code = forms.RegexField(r"^\d{6}$", label="Six-digit email code", max_length=6)


def send_challenge(user, purpose):
    if not user.email:
        raise ValueError("Add an email address first.")
    if Challenge.objects.filter(
        user=user, created_at__gte=timezone.now() - timedelta(minutes=1)
    ).exists():
        raise ValueError("Wait one minute before requesting another code.")
    code = f"{secrets.randbelow(1000000):06d}"
    Challenge.objects.filter(user=user, purpose=purpose, used=False).update(used=True)
    challenge = Challenge.objects.create(
        user=user,
        purpose=purpose,
        email=user.email,
        digest=make_password(code),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    try:
        sent = send_mail(
            "NexusLoan verification code",
            f"Your {purpose} code is {code}. Expires in 10 minutes. Do not share it.",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
        if not sent:
            raise ValueError("Email was not accepted by the delivery backend.")
    except Exception:
        challenge.used = True
        challenge.save(update_fields=["used"])
        raise ValueError(
            "Could not send a verification code. Check email delivery configuration."
        )
    return challenge


@transaction.atomic
def consume(challenge_id, code):
    challenge = Challenge.objects.select_for_update().filter(pk=challenge_id).first()
    if (
        not challenge
        or challenge.used
        or challenge.expires_at <= timezone.now()
        or challenge.attempts >= 5
    ):
        return None
    challenge.attempts += 1
    valid = check_password(code, challenge.digest)
    if valid:
        challenge.used = True
    challenge.save(update_fields=["attempts", "used"])
    return challenge if valid else None


class SecureLoginView(LoginView):
    template_name = "accounts/login.html"

    def post(self, request, *args, **kwargs):
        # Username-only bucket avoids trusting spoofable proxy headers.
        self.attempt_key = hashlib.sha256(
            request.POST.get("username", "").strip().lower().encode()
        ).hexdigest()
        recent = LoginAttempt.objects.filter(
            key=self.attempt_key,
            successful=False,
            created_at__gte=timezone.now() - timedelta(minutes=15),
        ).count()
        if recent >= 5:
            form = self.get_form()
            form.add_error(None, "Too many failed attempts. Try again in 15 minutes.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        LoginAttempt.objects.create(key=self.attempt_key)
        return super().form_invalid(form)

    def form_valid(self, form):
        user = form.get_user()
        security, _ = SecuritySetting.objects.get_or_create(user=user)
        if security.two_factor:
            identity = (
                Identity.objects.filter(user=user, verified_email=user.email)
                .exclude(verified_email="")
                .first()
            )
            if not identity:
                form.add_error(
                    None,
                    "Verified email no longer matches. Contact an administrator for recovery.",
                )
                return super().form_invalid(form)
            try:
                challenge = send_challenge(user, "login")
            except ValueError as exc:
                form.add_error(None, str(exc))
                return super().form_invalid(form)
            self.request.session["login_challenge"] = challenge.pk
            self.request.session["login_next"] = self.get_success_url()
            return redirect("operations:challenge")
        LoginAttempt.objects.create(key=self.attempt_key, successful=True)
        return super().form_valid(form)


def challenge_view(request):
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        challenge = consume(
            request.session.get("login_challenge"), form.cleaned_data["code"]
        )
        if (
            challenge
            and challenge.purpose == "login"
            and challenge.user.is_active
            and challenge.email == challenge.user.email
        ):
            login(
                request,
                challenge.user,
                backend="django.contrib.auth.backends.ModelBackend",
            )
            request.session.pop("login_challenge", None)
            return redirect(request.session.pop("login_next", "/dashboard/"))
        form.add_error(
            "code",
            "Invalid, expired, or exhausted code. Sign in again to request another.",
        )
    return render(
        request,
        "registration/form.html",
        {"form": form, "title": "Verify your sign-in", "button": "Verify"},
    )


@login_required
def security_view(request):
    identity, _ = Identity.objects.get_or_create(user=request.user)
    security, _ = SecuritySetting.objects.get_or_create(user=request.user)
    form = CodeForm(request.POST or None)
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("verify_email", "enable_2fa", "disable_2fa"):
            if action == "enable_2fa" and identity.verified_email != request.user.email:
                messages.error(request, "Verify your current email address first.")
            else:
                try:
                    challenge = send_challenge(request.user, action)
                    request.session["security_challenge"] = challenge.pk
                    messages.success(
                        request, "Code sent using the configured email backend."
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
        elif action == "confirm" and form.is_valid():
            challenge = consume(
                request.session.get("security_challenge"), form.cleaned_data["code"]
            )
            if (
                challenge
                and challenge.user_id == request.user.pk
                and challenge.email == request.user.email
            ):
                if challenge.purpose == "verify_email":
                    identity.verified_email = request.user.email
                    identity.save()
                elif challenge.purpose in ("enable_2fa", "disable_2fa"):
                    security.two_factor = challenge.purpose == "enable_2fa"
                    security.save()
                messages.success(request, "Account security updated.")
            else:
                messages.error(request, "Invalid or expired code.")
        elif action == "revoke":
            from django.contrib.sessions.models import Session

            record = LoginSession.objects.filter(
                pk=request.POST.get("session_id"), user=request.user
            ).first()
            if record:
                Session.objects.filter(session_key=record.session_key).delete()
                record.revoked = True
                record.save()
        return redirect("operations:security")
    return render(
        request,
        "operations/security.html",
        {
            "identity": identity,
            "security": security,
            "form": form,
            "sessions": LoginSession.objects.filter(user=request.user).order_by("-id")[
                :30
            ],
        },
    )


@receiver(user_logged_in)
def record_session(sender, request, user, **kwargs):
    if request and request.session.session_key:
        LoginSession.objects.create(
            user=user,
            session_key=request.session.session_key,
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
        )

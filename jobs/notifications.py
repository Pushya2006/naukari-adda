
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from users.models import StudentProfile
from .models import Notification

logger = logging.getLogger(__name__)


def user_display_name(user):
    profile = StudentProfile.objects.filter(user=user).only(
        'full_name').first()
    full_name = (getattr(profile, 'full_name', '') or '').strip()
    if full_name:
        return full_name
    account_name = ' '.join(filter(None, [
        (getattr(user, 'first_name', '') or '').strip(),
        (getattr(user, 'last_name', '') or '').strip(),
    ])).strip()
    return account_name or getattr(user, 'username', 'Student')


def personalize_body(user, body):
    name = user_display_name(user)
    body = (body or '').strip()
    if '{name}' in body:
        return body.replace('{name}', name)
    if body.lower().startswith(('hello ', 'hi ', 'dear ')):
        return body
    return f'Hello {name},\n\n{body}'


def notify_result(user, message, email_subject=None, email_body=None):
    Notification.objects.create(user=user, message=message)

    if not email_subject:
        return 'not_requested'

    recipient = (getattr(user, 'email', '') or '').strip()
    if not recipient:
        return 'no_email'

    try:
        validate_email(recipient)
    except ValidationError:
        return 'no_email'

    from_email = (getattr(settings, 'DEFAULT_FROM_EMAIL', None)
                  or getattr(settings, 'EMAIL_HOST_USER', None))
    if not from_email:
        logger.warning('DEFAULT_FROM_EMAIL is not configured.')
        return 'failed'

    try:
        sent_count = send_mail(
            subject=(email_subject or '').replace('\r', ' ').replace(
                '\n', ' ')[:200],
            message=personalize_body(user, email_body or message),
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
        if sent_count:
            return 'sent'
        logger.warning('Email backend did not send a message to %s.', recipient)
        return 'failed'
    except Exception:
        logger.exception('Email delivery failed for user %s.',
                         getattr(user, 'username', user))
        return 'failed'


def notify(user, message, email_subject=None, email_body=None):
    return notify_result(
        user, message, email_subject=email_subject,
        email_body=email_body) == 'sent'

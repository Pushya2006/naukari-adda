import json
import logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class BrevoEmailBackend(BaseEmailBackend):
    API_URL = "https://api.brevo.com/v3/smtp/email"

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        api_key = getattr(__import__("django.conf", fromlist=["settings"]).settings,
                          "BREVO_API_KEY", "")
        if not api_key:
            logger.error("BREVO_API_KEY is not configured.")
            return 0

        sent_count = 0

        for message in email_messages:
            payload = {
                "sender": {"email": message.from_email},
                "to": [{"email": email} for email in message.to],
                "subject": message.subject,
                "textContent": message.body,
            }

            if message.cc:
                payload["cc"] = [{"email": email} for email in message.cc]
            if message.bcc:
                payload["bcc"] = [{"email": email} for email in message.bcc]
            if message.reply_to:
                payload["replyTo"] = {"email": message.reply_to[0]}

            if message.alternatives:
                for content, mimetype in message.alternatives:
                    if mimetype == "text/html":
                        payload["htmlContent"] = content
                        break

            request = Request(
                self.API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "accept": "application/json",
                    "api-key": api_key,
                    "content-type": "application/json",
                },
                method="POST",
            )

            try:
                with urlopen(request, timeout=20) as response:
                    if response.status in (200, 201, 202):
                        sent_count += 1
            except HTTPError as exc:
                logger.error("Brevo rejected email. HTTP status: %s", exc.code)
            except (URLError, TimeoutError, OSError):
                logger.exception("Brevo email delivery failed.")

        return sent_count
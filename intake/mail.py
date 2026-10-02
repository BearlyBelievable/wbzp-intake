import dataclasses
import smtplib
import ssl
from email.message import EmailMessage

from . import config
from .errors import ConfigError, DeliveryError

SMTP_SECURITY_MODES = ("starttls", "ssl", "none")


@dataclasses.dataclass
class Email:
    to: str
    subject: str
    body: str
    bcc: list = dataclasses.field(default_factory=list)
    reply_to: str | None = None

    @property
    def recipients(self):
        return [self.to, *self.bcc]

    def to_message(self, from_addr):
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = self.to
        msg["Subject"] = self.subject
        if self.reply_to:
            msg["Reply-To"] = self.reply_to
        msg.set_content(self.body)
        return msg

    def send(self):
        smtp_config = SmtpConfig.resolve()
        msg = self.to_message(smtp_config.from_addr)
        context = ssl.create_default_context()
        try:
            if smtp_config.security == "ssl":
                connection = smtplib.SMTP_SSL(smtp_config.host, smtp_config.port, timeout=10, context=context)
            else:
                connection = smtplib.SMTP(smtp_config.host, smtp_config.port, timeout=10)
            with connection as smtp:
                if smtp_config.security == "starttls":
                    smtp.starttls(context=context)
                smtp.login(smtp_config.user, smtp_config.password)
                refused = smtp.send_message(msg, to_addrs=self.recipients)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
        except OSError as error:
            raise DeliveryError("send_failed", to=self.to, host=smtp_config.host, port=smtp_config.port, error=error) from error


def require_smtp(purpose, alternative):
    smtp_config = SmtpConfig.resolve()
    if not all([smtp_config.host, smtp_config.port, smtp_config.user, smtp_config.password]):
        raise ConfigError("smtp_required", purpose=purpose, alternative=alternative)
    if "@" not in smtp_config.from_addr:
        raise ConfigError("invalid_from_address", login=smtp_config.user)


@dataclasses.dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    security: str = "starttls"

    @classmethod
    def resolve(cls):
        port = config.read_config("smtp_port", default="")
        try:
            port = int(port) if port else 0
        except ValueError as error:
            raise ConfigError("invalid_setting", name="smtp_port", value=port) from error
        security = config.read_config("smtp_security", default="") or ("ssl" if port == 465 else "starttls")
        if security not in SMTP_SECURITY_MODES:
            raise ConfigError("invalid_setting", name="smtp_security", value=security)
        user = config.read_config("smtp_user", default="")
        return cls(
            host=config.read_config("smtp_host", default=""),
            port=port,
            user=user,
            password=config.read_secret("smtp_password", default=""),
            from_addr=config.read_config("smtp_from", default="") or user,
            security=security,
        )

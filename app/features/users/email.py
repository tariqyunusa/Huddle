import os
import resend

resend.api_key = os.environ["RESEND_API_KEY"]

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")


def send_password_reset_email(to_email: str, token: str):
    reset_link = f"{FRONTEND_URL}/reset-password?token={token}"
    result = resend.Emails.send({
        "from": "Huddle <onboarding@resend.dev>",
        "to": to_email,
        "subject": "Reset your Huddle password",
        "html": f"""
            <p>Someone requested a password reset for your Huddle account.</p>
            <p><a href="{reset_link}">Click here to reset your password</a></p>
            <p>This link expires in 30 minutes. If you didn't request this, ignore this email.</p>
        """,
    })
    print(f"RESEND RESULT: {result}")
    
def send_session_invite_email(to_email: str, inviter_name: str, session_id, session_title: str | None):
    join_link = f"{FRONTEND_URL}/?session={session_id}"
    title_text = session_title or "a Huddle session"
    resend.Emails.send({
        "from": "Huddle <onboarding@resend.dev>",
        "to": to_email,
        "subject": f"{inviter_name} invited you to {title_text}",
        "html": f"""
            <p>{inviter_name} invited you to join <strong>{title_text}</strong> on Huddle.</p>
            <p><a href="{join_link}">Click here to join</a></p>
        """,
    })
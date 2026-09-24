import resend
import os

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
HERO_IMAGE_URL = "https://huddle-6j42.onrender.com/assets/images/verify_email.jpg"


def build_verification_email_html(name: str, verify_link: str, expires_in_hours: int = 24) -> str:
    font_stack = "'Inter','Satoshi','Helvetica Neue',Helvetica,Arial,sans-serif"
    return f"""\
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <!--[if !mso]><!-->
    <link rel="stylesheet" href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap" />
    <!--<![endif]-->
  </head>
  <body style="margin:0; padding:0; background-color:#Ffffff; font-family:{font_stack};">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#FAF8EE;">
      <tr>
        <td align="center">
          <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px; padding:40px 32px; font-family:{font_stack};">

            <!-- Header -->
            <tr>
              <td>
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="color:#03482B; font-size:20px; font-weight:semi-bold; font-family:{font_stack};">huddle</td>
                    <td align="right" style="color:#03482B; font-size:12px; line-height:16px; font-family:{font_stack};">
                      Better work.<br />Common Middle ground.
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Hero image -->
            <tr>
              <td style="padding-top:24px;">
                <img src="{HERO_IMAGE_URL}" alt="Sunset over savanna" width="560"
                     style="width:100%; height:auto; border-radius:16px; display:block;" />
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding-top:32px;">
                <p style="color:#03482B; font-size:12px; font-weight:semi-bold; letter-spacing:1px; text-transform:uppercase; margin:0; font-family:{font_stack};">
                  Verify your account
                </p>
                <h1 style="color:#03482B; font-size:32px; line-height:1.15; font-weight:bold; margin:8px 0; font-family:{font_stack};">
                  Almost there, {name}.
                </h1>
                <p style="color:#03482B; font-size:16px; line-height:24px; font-family:{font_stack};">
                  Thanks for joining Huddle! To get started, please confirm your email address
                  by clicking the link below. This helps us keep your account secure.
                </p>
                <table cellpadding="0" cellspacing="0" style="margin-top:16px;">
                  <tr>
                    <td style="background-color:#03482B; border-radius:999px;">
                      <a href="{verify_link}"
                         style="display:inline-block; padding:14px 32px; color:#ffffff; font-size:15px; font-weight:500; text-decoration:none; font-family:{font_stack};">
                        Verify my email &rarr;
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="color:#03482B; font-size:12px; margin-top:16px; font-family:{font_stack};">
                  This link will expire in {expires_in_hours} hours.
                </p>
              </td>
            </tr>

            <!-- Divider -->
            <tr>
              <td style="padding:32px 0;">
                <hr style="border:none; border-top:1px solid #D8D4C6;" />
              </td>
            </tr>

            <!-- Help -->
            <tr>
              <td>
                <p style="color:#03482B; font-size:15px; font-weight:600; margin:0; font-family:{font_stack};">Need Help?</p>
                <p style="color:#03482B; font-size:14px; line-height:22px; margin-top:4px; font-family:{font_stack};">
                  If you didn't create this account, you can safely ignore this email or
                  <a href="mailto:support@huddle.app" style="color:#03482B; text-decoration:underline;">contact our team</a>.
                </p>
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="padding-top:40px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#DDE3D3; border-radius:16px; padding:24px;">
                  <tr>
                    <td style="padding:24px; font-family:{font_stack};">
                      <p style="color:#03482B; font-size:18px; font-weight:bold; margin:0; font-family:{font_stack};">huddle</p>
                      <p style="margin-top:8px;">
                        <a href="{FRONTEND_URL}/about" style="color:#03482B; font-size:12px; text-decoration:none; margin-right:16px; font-family:{font_stack};">About</a>
                        <a href="{FRONTEND_URL}/help" style="color:#03482B; font-size:12px; text-decoration:none; margin-right:16px; font-family:{font_stack};">Help</a>
                        <a href="{FRONTEND_URL}/blog" style="color:#03482B; font-size:12px; text-decoration:none; margin-right:16px; font-family:{font_stack};">Blog</a>
                        <a href="{FRONTEND_URL}/privacy" style="color:#03482B; font-size:12px; text-decoration:none; font-family:{font_stack};">Privacy</a>
                      </p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def send_verification_email(to_email: str, token: str, name: str = "there"):
    verify_link = f"{FRONTEND_URL}/verify-email?token={token}"
    resend.Emails.send({
        "from": "Huddle <onboarding@tariqyunusa.xyz>",
        "to": to_email,
        "subject": "Verify your Huddle email",
        "html": build_verification_email_html(name=name, verify_link=verify_link),
    })
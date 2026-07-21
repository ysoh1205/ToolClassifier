# create_user.py
import os

from supabase import create_client


admin = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SECRET_KEY"],
)

response = admin.auth.admin.create_user(
    {
        "email": "ysoh1205@g.skku.edu",
        "password": "__h_1_t__",
        "email_confirm": True,
    }
)

print(response.user.id)
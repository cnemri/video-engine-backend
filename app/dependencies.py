import logging
from fastapi import Header, HTTPException, Depends
from firebase_admin import auth
from .firebase import db

async def get_current_user(authorization: str = Header(...)):
    """
    Verifies the Firebase ID token and returns the user's UID.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authentication header")
    
    token = authorization.split("Bearer ")[1]
    try:
        # Verify the token with Firebase Admin
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        return uid
    except Exception as e:
        logging.error(f"Auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# Optional: Dependency that also fetches the user document from Firestore if needed
# async def get_current_user_data(uid: str = Depends(get_current_user)):
#     ...
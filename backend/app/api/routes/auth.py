from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    Role,
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, Token, UserOut

router = APIRouter(prefix='/auth', tags=['auth'])


@router.post('/login', response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')

    settings = get_settings()
    token = create_access_token(
        subject=str(user.id),
        role=user.role,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    return Token(access_token=token)


@router.get('/me', response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post('/bootstrap-admin', response_model=UserOut)
def bootstrap_admin(db: Session = Depends(get_db)) -> UserOut:
    import os
    import secrets as _secrets

    existing = db.query(User).count()
    if existing > 0:
        raise HTTPException(status_code=400, detail='Bootstrap already completed')

    # Require BOOTSTRAP_ADMIN_PASSWORD env var or generate a random one
    password = os.environ.get('BOOTSTRAP_ADMIN_PASSWORD', '') or _secrets.token_urlsafe(16)
    email = os.environ.get('BOOTSTRAP_ADMIN_EMAIL', 'admin@local.dev')

    admin = User(
        email=email,
        hashed_password=get_password_hash(password),
        role=Role.SUPER_ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return UserOut.model_validate(admin)

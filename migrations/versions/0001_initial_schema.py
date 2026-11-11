"""initial schema"""
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

CATEGORY_OWNER_FUNCTION = """
CREATE FUNCTION check_category_owner() RETURNS trigger AS $$
BEGIN
    IF NEW.category_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM category c
        WHERE c.id = NEW.category_id AND (c.user_id IS NULL OR c.user_id = NEW.user_id)
    ) THEN
        RAISE EXCEPTION 'category % is not available for user %', NEW.category_id, NEW.user_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

SYSTEM_CATEGORIES = [
    ("Entertainment", "Streaming, games and media", "#E74C3C"),
    ("Music", "Music services", "#9B59B6"),
    ("Software", "Applications and tools", "#3498DB"),
    ("Cloud", "Hosting and storage", "#1ABC9C"),
    ("Education", "Courses and learning", "#F1C40F"),
    ("Utilities", "Mobile, internet and utilities", "#E67E22"),
    ("Other", "Other expenses", "#95A5A6"),
]

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('user',
    sa.Column('pending_email', sa.String(length=320), nullable=True),
    sa.Column('pending_email_reserved_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('preferred_currency', sa.String(length=3), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('hashed_password', sa.String(length=1024), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_superuser', sa.Boolean(), nullable=False),
    sa.Column('is_verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("preferred_currency ~ '^[A-Z]{3}$'", name='ck_user_currency'),
    sa.CheckConstraint('email = lower(email)', name='ck_user_email_normalized'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_email'), 'user', ['email'], unique=True)
    op.create_index('uq_user_pending_email', 'user', [sa.text('lower(pending_email)')], unique=True, postgresql_where=sa.text('pending_email IS NOT NULL'))
    op.create_table('audit_log',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('entity_type', sa.String(length=32), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('old_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('new_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('request_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)
    op.create_table('auth_session',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
    sa.Column('token_family_id', sa.Uuid(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by_session_id', sa.Uuid(), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.ForeignKeyConstraint(['replaced_by_session_id'], ['auth_session.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('refresh_token_hash')
    )
    op.create_index(op.f('ix_auth_session_token_family_id'), 'auth_session', ['token_family_id'], unique=False)
    op.create_index(op.f('ix_auth_session_user_id'), 'auth_session', ['user_id'], unique=False)
    op.create_table('category',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('color', sa.String(length=7), nullable=False),
    sa.Column('is_system', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("color ~ '^#[0-9A-F]{6}$'", name='ck_category_color'),
    sa.CheckConstraint('(is_system AND user_id IS NULL) OR (NOT is_system AND user_id IS NOT NULL)', name='ck_category_owner'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_category_id_user')
    )
    op.create_index(op.f('ix_category_user_id'), 'category', ['user_id'], unique=False)
    op.create_index('uq_category_system_name', 'category', [sa.text('lower(name)')], unique=True, postgresql_where=sa.text('is_system'))
    op.create_index('uq_category_user_name', 'category', ['user_id', sa.text('lower(name)')], unique=True, postgresql_where=sa.text('NOT is_system'))
    op.create_table('idempotency_key',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('operation', sa.String(length=128), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('resource_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'operation', 'key', name='uq_idempotency_key')
    )
    op.create_index(op.f('ix_idempotency_key_expires_at'), 'idempotency_key', ['expires_at'], unique=False)
    op.create_table('one_time_token',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('token_type', sa.String(length=14), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("token_type IN ('verify_email', 'reset_password', 'change_email')", name='ck_token_type'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_one_time_token_user_id'), 'one_time_token', ['user_id'], unique=False)
    op.create_table('subscription',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('category_id', sa.Uuid(), nullable=True),
    sa.Column('service_name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('billing_interval_unit', sa.String(length=5), nullable=False),
    sa.Column('interval_count', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('next_payment_date', sa.Date(), nullable=False),
    sa.Column('schedule_anchor_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=9), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("billing_interval_unit IN ('day', 'week', 'month', 'year')", name='ck_subscription_unit'),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_subscription_currency'),
    sa.CheckConstraint("status IN ('active', 'paused', 'cancelled')", name='ck_subscription_status'),
    sa.CheckConstraint('amount > 0', name='ck_subscription_amount'),
    sa.CheckConstraint('interval_count BETWEEN 1 AND 1200', name='ck_subscription_interval'),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_subscription_id_user')
    )
    op.create_index(op.f('ix_subscription_category_id'), 'subscription', ['category_id'], unique=False)
    op.create_index('ix_subscription_user_next', 'subscription', ['user_id', 'next_payment_date'], unique=False)
    op.create_table('payment',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('subscription_id', sa.Uuid(), nullable=True),
    sa.Column('original_payment_id', sa.Uuid(), nullable=True),
    sa.Column('category_id', sa.Uuid(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('payment_type', sa.String(length=6), nullable=False),
    sa.Column('status', sa.String(length=9), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('billing_period_start', sa.Date(), nullable=True),
    sa.Column('service_name_snapshot', sa.String(length=200), nullable=False),
    sa.Column('category_snapshot', sa.String(length=100), nullable=True),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(payment_type = 'refund') = (original_payment_id IS NOT NULL)", name='ck_payment_refund_link'),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_payment_currency'),
    sa.CheckConstraint("payment_type IN ('charge', 'refund')", name='ck_payment_type'),
    sa.CheckConstraint("source IN ('manual', 'import', 'provider')", name='ck_payment_source'),
    sa.CheckConstraint("status IN ('succeeded', 'pending', 'failed', 'reversed')", name='ck_payment_status'),
    sa.CheckConstraint('amount > 0', name='ck_payment_amount'),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], ),
    sa.ForeignKeyConstraint(['original_payment_id', 'user_id'], ['payment.id', 'payment.user_id'], name='fk_payment_original_owner'),
    sa.ForeignKeyConstraint(['subscription_id', 'user_id'], ['subscription.id', 'subscription.user_id'], name='fk_payment_subscription_owner'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='uq_payment_id_user')
    )
    op.create_index('ix_payment_original', 'payment', ['original_payment_id'], unique=False)
    op.create_index('ix_payment_subscription', 'payment', ['subscription_id'], unique=False)
    op.create_index('ix_payment_user_occurred', 'payment', ['user_id', 'occurred_at'], unique=False)
    op.create_index('uq_payment_billing_period', 'payment', ['subscription_id', 'billing_period_start'], unique=True, postgresql_where=sa.text("payment_type = 'charge' AND status IN ('pending', 'succeeded') AND subscription_id IS NOT NULL AND billing_period_start IS NOT NULL"))
    op.execute(CATEGORY_OWNER_FUNCTION)
    for table in ("subscription", "payment"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_category_owner BEFORE INSERT OR UPDATE OF category_id, user_id "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION check_category_owner()"
        )
    category = sa.table(
        "category",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("color", sa.String()),
        sa.column("is_system", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        category,
        [
            {"id": uuid.uuid4(), "name": n, "description": d, "color": c, "is_system": True, "is_active": True}
            for n, d, c in SYSTEM_CATEGORIES
        ],
    )


def downgrade() -> None:
    for table in ("subscription", "payment"):
        op.execute(f"DROP TRIGGER trg_{table}_category_owner ON {table}")
    op.execute("DROP FUNCTION check_category_owner()")
    op.drop_index('uq_payment_billing_period', table_name='payment', postgresql_where=sa.text("payment_type = 'charge' AND status IN ('pending', 'succeeded') AND subscription_id IS NOT NULL AND billing_period_start IS NOT NULL"))
    op.drop_index('ix_payment_user_occurred', table_name='payment')
    op.drop_index('ix_payment_subscription', table_name='payment')
    op.drop_index('ix_payment_original', table_name='payment')
    op.drop_table('payment')
    op.drop_index('ix_subscription_user_next', table_name='subscription')
    op.drop_index(op.f('ix_subscription_category_id'), table_name='subscription')
    op.drop_table('subscription')
    op.drop_index(op.f('ix_one_time_token_user_id'), table_name='one_time_token')
    op.drop_table('one_time_token')
    op.drop_index(op.f('ix_idempotency_key_expires_at'), table_name='idempotency_key')
    op.drop_table('idempotency_key')
    op.drop_index('uq_category_user_name', table_name='category', postgresql_where=sa.text('NOT is_system'))
    op.drop_index('uq_category_system_name', table_name='category', postgresql_where=sa.text('is_system'))
    op.drop_index(op.f('ix_category_user_id'), table_name='category')
    op.drop_table('category')
    op.drop_index(op.f('ix_auth_session_user_id'), table_name='auth_session')
    op.drop_index(op.f('ix_auth_session_token_family_id'), table_name='auth_session')
    op.drop_table('auth_session')
    op.drop_index(op.f('ix_audit_log_user_id'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index('uq_user_pending_email', table_name='user', postgresql_where=sa.text('pending_email IS NOT NULL'))
    op.drop_index(op.f('ix_user_email'), table_name='user')
    op.drop_table('user')

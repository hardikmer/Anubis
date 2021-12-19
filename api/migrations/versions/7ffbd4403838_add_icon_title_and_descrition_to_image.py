"""ADD icon, title and descrition to image

Revision ID: 7ffbd4403838
Revises: 58bdacf47c81
Create Date: 2021-12-18 19:05:43.922111

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "7ffbd4403838"
down_revision = "58bdacf47c81"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "theia_image",
        sa.Column("title", sa.String(length=1024)),
    )
    op.add_column(
        "theia_image", sa.Column("description", sa.Text())
    )
    op.add_column(
        "theia_image",
        sa.Column("icon", sa.String(length=1024)),
    )
    conn = op.get_bind()
    with conn.begin():
        conn.execute('UPDATE theia_image SET `title` = `label`;')
        conn.execute('UPDATE theia_image SET `description` = `label`;')
    op.drop_column("theia_image", "label")
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "theia_image",
        sa.Column(
            "label",
            mysql.VARCHAR(collation="utf8mb4_unicode_ci", length=1024),
            nullable=False,
        ),
    )
    conn = op.get_bind()
    with conn.begin():
        conn.execute('UPDATE theia_image SET `label` = `title`;')
    op.drop_column("theia_image", "icon")
    op.drop_column("theia_image", "description")
    op.drop_column("theia_image", "title")
    # ### end Alembic commands ###

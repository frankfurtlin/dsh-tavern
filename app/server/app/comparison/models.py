from datetime import datetime
from sqlalchemy import Integer,DateTime,String,UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from ..models import Base,Owned,now


class ComparisonExposure(Owned,Base):
    """Explicit view range, separate from the reading cursor and world head."""
    __tablename__="comparison_exposures"
    __table_args__=(UniqueConstraint("owner_id","scope_key"),)
    scope_key:Mapped[str]=mapped_column(String(64))
    kind:Mapped[str]=mapped_column(String(20))
    resource_id:Mapped[str]=mapped_column(String(32),index=True)
    line_id:Mapped[str]=mapped_column(String(32),index=True)
    node_id:Mapped[str]=mapped_column(String(32))
    anchor:Mapped[dict]=mapped_column(JSONB,default=dict)
    version:Mapped[int]=mapped_column(Integer,default=1)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

from sqlalchemy import create_engine, Column, String, Text, TIMESTAMP, BigInteger, JSON, ForeignKey, Enum as SAEnum, text, Boolean, Numeric, Integer, select
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum
import os

DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'

engine = create_engine(DATABASE_URL)
Base = declarative_base()


class TaskStatus(str, enum.Enum):
    queued = 'queued'
    running = 'running'
    analyzing = 'analyzing'
    formatting = 'formatting'
    done = 'done'
    error = 'error'


class User(Base):
    __tablename__ = 'users'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_number = Column(BigInteger, unique=True, server_default=text("nextval('users_user_number_seq'::regclass)"))
    keycloak_id = Column(Text, unique=True, nullable=False)
    username = Column(Text)
    email = Column(Text)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_analyst = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    research_tasks = relationship('ResearchTask', back_populates='user')
    user_groups = relationship('UserGroup', back_populates='user')


class ResearchTask(Base):
    __tablename__ = 'research_tasks'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    task_number = Column(BigInteger, unique=True, server_default=text("nextval('research_tasks_task_number_seq'::regclass)"))
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    prompt = Column(Text, nullable=False)
    status = Column(SAEnum(TaskStatus), nullable=False, default=TaskStatus.queued)
    priority = Column(BigInteger, default=0)
    model_used = Column(Text)
    meta = Column(JSONB)
    error_message = Column(Text)
    logs = Column(JSONB)
    created_at = Column(TIMESTAMP, server_default=func.now())
    started_at = Column(TIMESTAMP)
    finished_at = Column(TIMESTAMP)

    user = relationship('User', back_populates='research_tasks')
    report = relationship('ResearchReport', back_populates='task', uselist=False, cascade='all, delete-orphan', passive_deletes=True)
    events = relationship('AgentEvent', back_populates='task', cascade='all, delete-orphan', passive_deletes=True)


class ResearchReport(Base):
    __tablename__ = 'research_reports'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    report_number = Column(BigInteger, unique=True, server_default=text("nextval('research_reports_report_number_seq'::regclass)"))
    task_id = Column(UUID(as_uuid=True), ForeignKey('research_tasks.id', ondelete='CASCADE'), unique=True, nullable=False)
    report_json = Column(JSONB, nullable=False)
    sources = Column(JSONB)
    score = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, server_default=func.now())

    task = relationship('ResearchTask', back_populates='report', passive_deletes=True)


class AgentEvent(Base):
    __tablename__ = 'agent_events'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    event_number = Column(BigInteger, unique=True, server_default=text("nextval('agent_events_event_number_seq'::regclass)"))
    task_id = Column(UUID(as_uuid=True), ForeignKey('research_tasks.id', ondelete='CASCADE'), nullable=False)
    agent_name = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    message = Column(Text)
    meta = Column(JSONB)
    elapsed_seconds = Column(Numeric(10, 2))
    created_at = Column(TIMESTAMP, server_default=func.now())

    task = relationship('ResearchTask', back_populates='events')


class LLMProvider(Base):
    __tablename__ = 'llm_providers'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    provider_number = Column(BigInteger, unique=True, server_default=text("nextval('llm_providers_provider_number_seq'::regclass)"))
    name = Column(Text, unique=True, nullable=False)
    provider_type = Column(Text, nullable=False)
    base_url = Column(Text, nullable=False)
    api_key = Column(Text)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

    models = relationship('LLMModel', back_populates='provider')


class LLMModel(Base):
    __tablename__ = 'llm_models'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    model_number = Column(BigInteger, unique=True, server_default=text("nextval('llm_models_model_number_seq'::regclass)"))
    provider_id = Column(UUID(as_uuid=True), ForeignKey('llm_providers.id', ondelete='CASCADE'), nullable=False)
    model_name = Column(Text, nullable=False)
    display_name = Column(Text)
    context_size = Column(Integer)
    max_tokens = Column(Integer)
    temperature = Column(Numeric(3, 2))
    enabled = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=100)
    timeout = Column(Integer, nullable=False, default=180)
    created_at = Column(TIMESTAMP, server_default=func.now())

    provider = relationship('LLMProvider', back_populates='models')
    group_models = relationship('GroupModel', back_populates='model', passive_deletes=True)
    fallbacks = relationship('LLMFallback', foreign_keys='LLMFallback.model_id', back_populates='model', passive_deletes=True)
    fallback_for = relationship('LLMFallback', foreign_keys='LLMFallback.fallback_model_id', back_populates='fallback_model', passive_deletes=True)

class LLMFallback(Base):
    __tablename__ = 'llm_fallback'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    relation_number = Column(BigInteger, unique=True, server_default=text("nextval('llm_fallback_relation_number_seq'::regclass)"))
    model_id = Column(UUID(as_uuid=True), ForeignKey('llm_models.id', ondelete='CASCADE'), nullable=False)
    fallback_model_id = Column(UUID(as_uuid=True), ForeignKey('llm_models.id', ondelete='CASCADE'), nullable=False)
    priority = Column(Integer, nullable=False, default=1)

    model = relationship('LLMModel', foreign_keys=[model_id], back_populates='fallbacks')
    fallback_model = relationship('LLMModel', foreign_keys=[fallback_model_id], back_populates='fallback_for')


class LLMGroup(Base):
    __tablename__ = 'llm_groups'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    group_number = Column(BigInteger, unique=True, server_default=text("nextval('llm_groups_group_number_seq'::regclass)"))
    name = Column(Text, unique=True, nullable=False)
    description = Column(Text)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

    user_groups = relationship('UserGroup', back_populates='group')
    group_models = relationship('GroupModel', back_populates='group')


class UserGroup(Base):
    __tablename__ = 'user_groups'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    relation_number = Column(BigInteger, unique=True, server_default=text("nextval('user_groups_relation_number_seq'::regclass)"))
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey('llm_groups.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    user = relationship('User', back_populates='user_groups')
    group = relationship('LLMGroup', back_populates='user_groups')


class GroupModel(Base):
    __tablename__ = 'group_models'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    relation_number = Column(BigInteger, unique=True, server_default=text("nextval('group_models_relation_number_seq'::regclass)"))
    group_id = Column(UUID(as_uuid=True), ForeignKey('llm_groups.id', ondelete='CASCADE'), nullable=False)
    model_id = Column(UUID(as_uuid=True), ForeignKey('llm_models.id', ondelete='CASCADE'), nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    group = relationship('LLMGroup', back_populates='group_models')
    model = relationship('LLMModel', back_populates='group_models')


class ResearchScore(Base):
    __tablename__ = 'research_scores'

    score = Column(Integer, primary_key=True)
    color = Column(Text, nullable=False)


def get_score_color(score_val, db_session):
    row = db_session.execute(
        select(ResearchScore.color)
        .where(ResearchScore.score >= score_val)
        .order_by(ResearchScore.score.asc())
        .limit(1)
    ).scalar()
    return row or '#555555'


def init_db():
    Base.metadata.create_all(engine)

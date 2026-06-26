from sqlalchemy import create_engine, Column, String, Text, TIMESTAMP, BigInteger, JSON, ForeignKey, Enum as SAEnum, text, Boolean, Numeric, Integer
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


class ResearchTask(Base):
    __tablename__ = 'research_tasks'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    task_number = Column(BigInteger, unique=True, server_default=text("nextval('research_tasks_task_number_seq'::regclass)"))
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    prompt = Column(Text, nullable=False)
    status = Column(SAEnum(TaskStatus), nullable=False, default=TaskStatus.queued)
    priority = Column(BigInteger, default=0)
    model_used = Column(Text)
    error_message = Column(Text)
    created_at = Column(TIMESTAMP, server_default=func.now())
    started_at = Column(TIMESTAMP)
    finished_at = Column(TIMESTAMP)

    user = relationship('User', back_populates='research_tasks')
    report = relationship('ResearchReport', back_populates='task', uselist=False)
    events = relationship('AgentEvent', back_populates='task')


class ResearchReport(Base):
    __tablename__ = 'research_reports'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    report_number = Column(BigInteger, unique=True, server_default=text("nextval('research_reports_report_number_seq'::regclass)"))
    task_id = Column(UUID(as_uuid=True), ForeignKey('research_tasks.id', ondelete='CASCADE'), unique=True, nullable=False)
    report_json = Column(JSONB, nullable=False)
    sources = Column(JSONB)
    created_at = Column(TIMESTAMP, server_default=func.now())

    task = relationship('ResearchTask', back_populates='report')


class AgentEvent(Base):
    __tablename__ = 'agent_events'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    event_number = Column(BigInteger, unique=True, server_default=text("nextval('agent_events_event_number_seq'::regclass)"))
    task_id = Column(UUID(as_uuid=True), ForeignKey('research_tasks.id', ondelete='CASCADE'), nullable=False)
    agent_name = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    message = Column(Text)
    meta = Column(JSONB)
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
    created_at = Column(TIMESTAMP, server_default=func.now())

    provider = relationship('LLMProvider', back_populates='models')


def init_db():
    Base.metadata.create_all(engine)

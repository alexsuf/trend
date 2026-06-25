from sqlalchemy import create_engine, Column, String, Text, TIMESTAMP, BigInteger, Boolean, Integer, Numeric, ForeignKey, text, func
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import UUID
import os

DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'

engine = create_engine(DATABASE_URL)
Base = declarative_base()


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

    user_groups = relationship('UserGroup', back_populates='user')


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
    group_models = relationship('GroupModel', back_populates='model')
    fallbacks = relationship('LLMFallback', foreign_keys='LLMFallback.model_id', back_populates='model')
    fallback_for = relationship('LLMFallback', foreign_keys='LLMFallback.fallback_model_id', back_populates='fallback_model')
    agent_models = relationship('AgentModel', back_populates='model')


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


class LLMFallback(Base):
    __tablename__ = 'llm_fallback'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    relation_number = Column(BigInteger, unique=True, server_default=text("nextval('llm_fallback_relation_number_seq'::regclass)"))
    model_id = Column(UUID(as_uuid=True), ForeignKey('llm_models.id', ondelete='CASCADE'), nullable=False)
    fallback_model_id = Column(UUID(as_uuid=True), ForeignKey('llm_models.id', ondelete='CASCADE'), nullable=False)
    priority = Column(Integer, nullable=False, default=1)

    model = relationship('LLMModel', foreign_keys=[model_id], back_populates='fallbacks')
    fallback_model = relationship('LLMModel', foreign_keys=[fallback_model_id], back_populates='fallback_for')


class AgentModel(Base):
    __tablename__ = 'agent_models'

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    relation_number = Column(BigInteger, unique=True, server_default=text("nextval('agent_models_relation_number_seq'::regclass)"))
    agent_name = Column(Text, nullable=False, unique=True)
    model_id = Column(UUID(as_uuid=True), ForeignKey('llm_models.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    model = relationship('LLMModel', back_populates='agent_models')


def init_db():
    Base.metadata.create_all(engine)

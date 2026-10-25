"""Public inventory of initialized instances; never serialize a config or contact a provider."""
from collections.abc import Mapping


class CatalogUnavailable(Exception):
    pass


def field(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CatalogUnavailable()
    # A model identifier is not an endpoint; reject URLs with credentials/query.
    if '://' in value or '?' in value or '#' in value:
        raise CatalogUnavailable()
    return value


def catalog(components):
    if not isinstance(components, Mapping):
        raise CatalogUnavailable()
    reader = components.get('mem_reader')
    if reader is None:
        raise CatalogUnavailable()
    records = []

    def add(instance, role, source, group='memory', name=None, required=False):
        if instance is None:
            if required:
                raise CatalogUnavailable()
            return
        config = getattr(instance, 'config', None)
        name = name or field(config, 'model_name_or_path') or field(config, 'model') or getattr(instance, 'model', None)
        if name is None:
            if required:
                raise CatalogUnavailable()
            return  # e.g. local cosine reranking has no model; do not invent one.
        model = text(name)
        spec = type(instance).__name__
        dims = field(config, 'embedding_dims')
        if isinstance(dims, int) and not isinstance(dims, bool) and 0 < dims <= 262144:
            spec += ' · ' + str(dims) + ' dimensions'
        records.append({'model': model, 'role': role, 'source': source, 'spec': spec, 'group': group})
        if len(records) > 64:
            raise CatalogUnavailable()

    add(components.get('llm'), '记忆管理', 'components.llm', required=True)
    add(getattr(reader, 'llm', None), '记忆抽取', 'components.mem_reader.llm', required=True)
    add(components.get('embedder'), '向量化', 'components.embedder', required=True)
    add(components.get('reranker'), '重排', 'components.reranker')
    for name, role in [('general_llm', '通用记忆处理'), ('image_parser_llm', '图像解析'),
                       ('document_parser_llm', '文档解析'), ('preference_extractor_llm', '偏好抽取（已初始化）')]:
        add(getattr(reader, name, None), role, 'components.mem_reader.' + name)
    # Keys are the actual supported model names in the initialized chat registry,
    # not the client object's default model. None means chat is disabled.
    for name, role in [('chat_llms', 'API对话'), ('playground_chat_llms', 'Playground对话')]:
        mapping = components.get(name)
        if mapping is None:
            continue
        if not isinstance(mapping, Mapping):
            raise CatalogUnavailable()
        for model, instance in sorted(mapping.items()):
            add(instance, role, 'components.' + name + '/' + text(model), 'chat', model, required=True)
    return {'records': records}

"""Native Secret Service items, retaining the Go client's collection choice."""
from contextlib import contextmanager, closing


@contextmanager
def target(service, user):
    import secretstorage
    with closing(secretstorage.dbus_init()) as connection:
        try:
            collection = secretstorage.Collection(connection, '/org/freedesktop/secrets/collection/login')
        except secretstorage.ItemNotFoundException:
            collection = secretstorage.get_default_collection(connection)
        collection.ensure_not_locked()
        items = list(collection.search_items({'service': service, 'username': user}))
        if len(items) > 1:
            raise ValueError('原生登录凭据存在多个匹配项，请先在原客户端处理')
        item = items[0] if items else None
        if item:
            item.ensure_not_locked()
        yield collection, item


def read(service, user):
    with target(service, user) as (_, item):
        return item.get_secret() if item else None


def write(service, user, value):
    with target(service, user) as (collection, item):
        if value is None:
            if item:
                item.delete()
        elif item:
            item.set_secret(value)
        else:
            collection.create_item("Password for '%s' on '%s'" % (user, service),
                                   {'service': service, 'username': user}, value, replace=True)

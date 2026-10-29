"""Optional facade. Actor IDs must come from the host's authenticated UserService."""

from arcade_helper.core.errors import StorageNotConfiguredError


class AliasService:
    def __init__(self, catalog_store=None, *, policy=None, daily_limit=20, pending_limit=5):
        if any(type(value) is not int or value < 1 for value in (daily_limit, pending_limit)):
            raise ValueError("Submission limits must be positive integers")
        self.catalog_store = catalog_store
        self.policy = policy
        self.daily_limit, self.pending_limit = daily_limit, pending_limit
        self._store = None

    def __getattr__(self, name):
        if name not in {"configure_source", "source_versions", "sync_source", "get_effective_aliases", "search_all",
                        "resolve_alias", "submit", "get_request", "list_requests", "withdraw", "supersede", "review",
                        "list_candidates", "decide_candidate", "associate", "events", "get_marker_aliases", "expand_query"}:
            raise AttributeError(name)
        if self.catalog_store is None or not self.catalog_store.enable_aliases:
            raise StorageNotConfiguredError("Enable aliases on a persistent catalog store first")
        if self._store is None:
            from arcade_helper.storage.tortoise.alias_store import TortoiseAliasStore
            self._store = TortoiseAliasStore(self.catalog_store, policy=self.policy,
                                            daily_limit=self.daily_limit, pending_limit=self.pending_limit)
        return getattr(self._store, name)

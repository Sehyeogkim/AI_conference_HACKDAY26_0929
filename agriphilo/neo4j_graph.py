"""Optional Neo4j AuraDB index for WeFarm tasks and episode metadata.

Recordings stay in file storage. The graph stores a URI, digest, and QA status;
the marketplace's local index remains usable when AuraDB is unconfigured.
Install ``neo4j>=5`` to enable the official Bolt driver.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from .brainbase import load_dotenv


def _str(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


class GraphStore:
    def __init__(self, driver: Any = None, database: str = "neo4j") -> None:
        self.driver = driver
        self.database = database
        self.last_error: str | None = None

    @classmethod
    def from_env(cls) -> "GraphStore":
        """Create a lazy driver if configured; no query is made here."""
        load_dotenv()
        uri = os.getenv("NEO4J_URI")
        username = os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER")
        password = os.getenv("NEO4J_PASSWORD") or os.getenv("NEO4J_PW")
        database = os.getenv("NEO4J_DATABASE") or "neo4j"
        if not all((uri, username, password)):
            result = cls(database=database)
            result.last_error = "Neo4j credentials are not configured"
            return result
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(uri, auth=(username, password), connection_timeout=5)
            return cls(driver, database)
        except (ImportError, ValueError):
            result = cls(database=database)
            result.last_error = "Neo4j driver is unavailable or configuration is invalid"
            return result

    @property
    def configured(self) -> bool:
        return self.driver is not None

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()

    def _execute(self, query: str, params: dict[str, Any]) -> bool:
        if self.driver is None:
            self.last_error = self.last_error or "Neo4j is unconfigured"
            return False
        try:
            self.driver.execute_query(query, parameters_=params, database_=self.database)
            self.last_error = None
            return True
        except Exception:
            # Never expose a URI or credentials in user-facing API responses.
            self.last_error = "Neo4j write failed"
            return False

    def _read(self, query: str, params: dict[str, Any]) -> list[Any] | None:
        if self.driver is None:
            self.last_error = self.last_error or "Neo4j is unconfigured"
            return None
        try:
            records, _, _ = self.driver.execute_query(query, parameters_=params, database_=self.database)
            self.last_error = None
            return records
        except Exception:
            self.last_error = "Neo4j query failed"
            return None

    def ensure_schema(self) -> bool:
        """Create id constraints if the connected account has schema privilege."""
        for label, key in (("Task", "task_id"), ("Episode", "episode_id"), ("QAResult", "qa_id"), ("Purchase", "purchase_id")):
            query = f"CREATE CONSTRAINT {label.lower()}_{key}_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
            if not self._execute(query, {}):
                return False
        return True

    def upsert_task(self, task: Mapping[str, Any]) -> bool:
        task_id = _str(task.get("task_id"), 120)
        if not task_id:
            self.last_error = "task_id is required"
            return False
        spec = task.get("task_spec") if isinstance(task.get("task_spec"), dict) else dict(task)
        properties = {
            "task_id": task_id,
            "schema_version": "wefarm-task-v1",
            "robot": _str(spec.get("robot"), 200),
            "environment": _str(spec.get("environment"), 200),
            "objective": _str(spec.get("objective"), 500),
            "requested_episodes": int(spec.get("requested_episodes", 1)),
            "source": _str(task.get("source"), 40),
            "image_uri": _str(task.get("image_uri"), 1000),
            "request_text": _str(spec.get("request_text"), 5000),
            "spec_json": json.dumps(spec, ensure_ascii=False, default=str)[:16000],
            "created_at": _str(task.get("created_at"), 80),
        }
        return self._execute("MERGE (t:Task {task_id: $id}) SET t += $properties", {"id": task_id, "properties": properties})

    def upsert_episode(self, episode: Mapping[str, Any], qa: Mapping[str, Any]) -> bool:
        episode_id = _str(episode.get("episode_id"), 120)
        task_id = _str(episode.get("task_id"), 120)
        if not episode_id or not task_id:
            self.last_error = "episode_id and task_id are required"
            return False
        structural = qa.get("structural_pass") is True
        replay = qa.get("replay_pass") is True
        ai_verdict = qa.get("verdict") or qa.get("ai_verdict") or "pending"
        approved = structural and replay and ai_verdict == "pass"
        qa_id = _str(qa.get("qa_id") or f"{episode_id}-qa", 140)
        recording_uri = _str(episode.get("recording_uri") or episode.get("hdf5_uri"), 1000)
        episode_props = {
            "episode_id": episode_id, "task_id": task_id,
            "player_id": _str(episode.get("player_id"), 120),
            "duration_s": float(episode.get("duration_s") or 0),
            "score": float(episode.get("score") or 0),
            "recording_uri": recording_uri,
            "recording_format": _str(episode.get("recording_format") or "jsonl.gz", 30),
            "sha256": _str(episode.get("sha256"), 64),
            "created_at": _str(episode.get("created_at"), 80),
            "approved": approved,
            "quality": int(episode.get("quality") or 0),
            "harvested_count": int(episode.get("harvested_count") or 0),
            "seed": int(episode.get("seed") or 0),
            "arm": _str(episode.get("arm"), 40),
        }
        qa_props = {
            "qa_id": qa_id, "episode_id": episode_id,
            "structural_pass": structural, "replay_pass": replay,
            "ai_verdict": _str(ai_verdict, 30),
            "reasons": [str(v)[:240] for v in qa.get("reasons", []) if isinstance(v, str)][:8],
            "model_id": _str(qa.get("model") or qa.get("model_id"), 200),
            "qa_version": _str(qa.get("qa_version"), 80),
            "evaluated_at": _str(qa.get("evaluated_at"), 80),
        }
        query = (
            "MERGE (t:Task {task_id: $task_id}) "
            "MERGE (e:Episode {episode_id: $episode_id}) SET e += $episode "
            "MERGE (t)-[:HAS_EPISODE]->(e) "
            "MERGE (q:QAResult {qa_id: $qa_id}) SET q += $qa "
            "MERGE (e)-[:HAS_QA]->(q)"
        )
        return self._execute(query, {
            "task_id": task_id, "episode_id": episode_id, "qa_id": qa_id,
            "episode": episode_props, "qa": qa_props,
        })

    def query_approved_episodes(self, task_id: str, filters: Mapping[str, Any] | None = None) -> list[str] | None:
        """Return approved episode IDs or ``None`` when AuraDB is unavailable.

        ``[]`` means the graph was queried successfully and no episode matched.
        Unsupported or malformed filters raise ``ValueError`` so the caller
        cannot accidentally fall back to an unfiltered result.
        """
        task_id = _str(task_id, 120)
        if not task_id:
            raise ValueError("task_id is required")
        filters = dict(filters or {})
        allowed = {"player_id", "min_quality", "max_duration_s", "seed", "arm", "min_harvested"}
        unknown = set(filters) - allowed
        if unknown:
            raise ValueError(f"unsupported episode filters: {', '.join(sorted(unknown))}")
        params: dict[str, Any] = {"task_id": task_id}
        for key in ("min_quality", "min_harvested", "seed"):
            value = filters.get(key)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f"{key} must be a nonnegative integer")
                params[key] = value
        duration = filters.get("max_duration_s")
        if duration is not None:
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= float(duration) < float("inf"):
                raise ValueError("max_duration_s must be a nonnegative finite number")
            params["max_duration_s"] = float(duration)
        for key in ("player_id", "arm"):
            value = filters.get(key)
            if value is not None:
                if not isinstance(value, str) or not value.strip() or len(value) > 120:
                    raise ValueError(f"{key} must be a short string")
                params[key] = value.strip()

        clauses = ["e.approved = true", "q.structural_pass = true", "q.replay_pass = true", "q.ai_verdict = 'pass'"]
        fixed_filters = {
            "player_id": "e.player_id = $player_id",
            "min_quality": "e.quality >= $min_quality",
            "max_duration_s": "e.duration_s <= $max_duration_s",
            "seed": "e.seed = $seed",
            "arm": "e.arm = $arm",
            "min_harvested": "e.harvested_count >= $min_harvested",
        }
        clauses.extend(expression for key, expression in fixed_filters.items() if key in params)
        query = (
            "MATCH (:Task {task_id: $task_id})-[:HAS_EPISODE]->(e:Episode)-[:HAS_QA]->(q:QAResult) "
            + "WHERE " + " AND ".join(clauses)
            + " RETURN DISTINCT e.episode_id AS episode_id ORDER BY episode_id"
        )
        records = self._read(query, params)
        return None if records is None else [str(record["episode_id"]) for record in records]

    def record_purchase(
        self,
        purchase_id: str,
        task_id: str,
        episode_ids: list[str],
        requester_id: str,
        credit_transaction_id: str,
    ) -> bool:
        """Link a purchase to every episode, only if all are QA-approved.

        This is a graph audit record, not the payment or download entitlement
        source. The marketplace must check its credit ledger again on download.
        """
        purchase_id = _str(purchase_id, 120)
        task_id = _str(task_id, 120)
        requester_id = _str(requester_id, 120)
        credit_transaction_id = _str(credit_transaction_id, 120)
        if not all((purchase_id, task_id, requester_id, credit_transaction_id)):
            self.last_error = "purchase, task, requester, and credit transaction IDs are required"
            return False
        if not isinstance(episode_ids, list) or not episode_ids or len(episode_ids) > 1000:
            self.last_error = "1 to 1000 episode IDs are required"
            return False
        ids = [_str(value, 120) for value in episode_ids]
        if any(not value for value in ids) or len(set(ids)) != len(ids):
            self.last_error = "episode IDs must be nonempty and unique"
            return False
        query = (
            "MATCH (:Task {task_id: $task_id})-[:HAS_EPISODE]->(e:Episode)-[:HAS_QA]->(q:QAResult) "
            "WHERE e.episode_id IN $episode_ids AND e.approved = true "
            "AND q.structural_pass = true AND q.replay_pass = true AND q.ai_verdict = 'pass' "
            "WITH collect(DISTINCT e) AS episodes "
            "WHERE size(episodes) = size($episode_ids) "
            "MERGE (p:Purchase {purchase_id: $purchase_id}) "
            "SET p.requester_id = $requester_id, p.task_id = $task_id, "
            "p.credit_transaction_id = $credit_transaction_id, p.purchased_at = datetime() "
            "FOREACH (e IN episodes | MERGE (p)-[:GRANTS_ACCESS_TO]->(e)) "
            "RETURN p.purchase_id AS purchase_id"
        )
        records = self._read(query, {
            "purchase_id": purchase_id, "task_id": task_id, "episode_ids": ids,
            "requester_id": requester_id, "credit_transaction_id": credit_transaction_id,
        })
        if records is None:
            return False
        if not records:
            self.last_error = "purchase episodes are missing or not approved"
            return False
        return True

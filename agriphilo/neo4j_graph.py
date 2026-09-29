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
        for label, key in (("Task", "task_id"), ("Episode", "episode_id"), ("QAResult", "qa_id"),
                           ("Purchase", "purchase_id"), ("Requester", "requester_id"), ("Player", "player_id")):
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
            "requested_episodes": int(spec.get("requested_episodes") or 1),
            "source": _str(task.get("source"), 40),
            "image_uri": _str(task.get("image_uri"), 1000),
            "request_text": _str(spec.get("request_text"), 5000),
            "spec_json": json.dumps(spec, ensure_ascii=False, default=str)[:16000],
            "created_at": _str(task.get("created_at"), 80),
            "requester_id": _str(task.get("requester_id"), 120),
            "status": _str(task.get("status") or "live", 40),
        }
        requester_id = properties["requester_id"]
        if requester_id:
            query = ("MERGE (t:Task {task_id: $id}) SET t += $properties "
                     "MERGE (r:Requester {requester_id: $requester_id}) "
                     "MERGE (r)-[:REQUESTED]->(t)")
        else:
            query = "MERGE (t:Task {task_id: $id}) SET t += $properties"
        return self._execute(query, {"id": task_id, "properties": properties, "requester_id": requester_id})

    def upsert_episode(self, episode: Mapping[str, Any], qa: Mapping[str, Any]) -> bool:
        episode_id = _str(episode.get("episode_id"), 120)
        task_id = _str(episode.get("task_id"), 120)
        if not episode_id or not task_id:
            self.last_error = "episode_id and task_id are required"
            return False
        gates = qa.get("rule_results") or qa.get("hard_gates") or {}
        if not isinstance(gates, Mapping):
            gates = {}
        structural = qa.get("structural_pass") is True
        replay = qa.get("replay_pass") is True
        ai_verdict = _str(qa.get("verdict") or qa.get("ai_verdict") or "pending", 30).lower()
        ai_source = _str(qa.get("ai_source") or qa.get("source"), 40).lower()
        status = _str(episode.get("qa_status") or qa.get("qa_status"), 40).lower()
        hard_gates_pass = all(gates.get(key) is True for key in (
            "data_integrity", "reproducibility", "task_completion", "uniqueness"))
        approved = (structural and replay and hard_gates_pass and ai_verdict == "accept"
                    and ai_source == "openrouter" and status == "approved")
        if not status:
            status = "approved" if approved else "pending_review"
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
            "trajectory_sha256": _str(episode.get("trajectory_sha256"), 64),
            "created_at": _str(episode.get("created_at"), 80),
            "approved": approved,
            "qa_status": status,
            "submitted_at": _str(episode.get("submitted_at") or episode.get("created_at"), 80),
            "qa_started_at": _str(episode.get("qa_started_at") or qa.get("started_at"), 80),
            "qa_completed_at": _str(episode.get("qa_completed_at") or qa.get("evaluated_at"), 80),
            "quality": int(episode.get("quality") or 0),
            "harvested_count": int(episode.get("harvested_count") or 0),
            "seed": int(episode.get("seed") or 0),
            "arm": _str(episode.get("arm"), 40),
        }
        qa_props = {
            "qa_id": qa_id, "episode_id": episode_id,
            "structural_pass": structural, "replay_pass": replay,
            "ai_verdict": _str(ai_verdict, 30),
            "ai_source": ai_source,
            "qa_status": status,
            "reasons": [str(v)[:240] for v in (qa.get("reasons") or []) if isinstance(v, str)][:8],
            "model_id": _str(qa.get("model") or qa.get("model_id"), 200),
            "qa_version": _str(qa.get("qa_version"), 80),
            "evaluated_at": _str(qa.get("evaluated_at"), 80),
            "explanation": _str(qa.get("explanation") or qa.get("reason"), 2000),
            "rule_results_json": json.dumps(gates, ensure_ascii=False, default=str)[:12000],
            "evidence_json": json.dumps(qa.get("evidence") or {}, ensure_ascii=False, default=str)[:16000],
            "model_response_json": json.dumps(qa.get("model_response") or {}, ensure_ascii=False, default=str)[:12000],
        }
        player_id = episode_props["player_id"]
        query = ("MERGE (t:Task {task_id: $task_id}) "
                 "MERGE (e:Episode {episode_id: $episode_id}) SET e += $episode "
                 "MERGE (t)-[:HAS_EPISODE]->(e) "
                 "MERGE (q:QAResult {qa_id: $qa_id}) SET q += $qa "
                 "MERGE (e)-[:HAS_QA]->(q) ")
        if player_id:
            query += ("MERGE (p:Player {player_id: $player_id}) "
                      "MERGE (p)-[:PLAYED]->(e)")
        return self._execute(query, {
            "task_id": task_id, "episode_id": episode_id, "qa_id": qa_id,
            "episode": episode_props, "qa": qa_props, "player_id": player_id,
        })

    def query_task_episodes(self, task_id: str) -> list[dict[str, Any]] | None:
        """Read the task's stored episode and QA metadata for the requester view.

        ``None`` means Neo4j could not be queried. The recording remains in
        container file storage; only its URI and digest are kept in the graph.
        """
        task_id = _str(task_id, 120)
        if not task_id:
            raise ValueError("task_id is required")
        query = (
            "MATCH (:Task {task_id: $task_id})-[:HAS_EPISODE]->(e:Episode) "
            "OPTIONAL MATCH (e)-[:HAS_QA]->(q:QAResult) "
            "RETURN properties(e) AS episode, properties(q) AS qa"
        )
        records = self._read(query, {"task_id": task_id})
        if records is None:
            return None
        episode_keys = ("episode_id", "player_id", "recording_uri", "recording_format", "sha256",
                        "trajectory_sha256", "submitted_at", "duration_s", "harvested_count",
                        "quality", "qa_status", "approved")
        qa_keys = ("structural_pass", "replay_pass", "ai_verdict", "ai_source", "model_id",
                   "qa_version", "reasons", "explanation", "evaluated_at")
        result = []
        for record in records:
            episode = record["episode"] or {}
            qa = record["qa"] or {}
            result.append({**{key: episode.get(key) for key in episode_keys},
                           **{key: qa.get(key) for key in qa_keys}})
        def submitted_at(item: dict[str, Any]) -> float:
            try:
                return float(item.get("submitted_at") or 0)
            except (TypeError, ValueError):
                return 0.0

        result.sort(key=submitted_at, reverse=True)
        return result

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

        clauses = ["e.approved = true", "e.qa_status = 'approved'", "q.structural_pass = true",
                   "q.replay_pass = true", "q.ai_verdict = 'accept'", "q.ai_source = 'openrouter'"]
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
            "AND e.qa_status = 'approved' AND q.structural_pass = true AND q.replay_pass = true "
            "AND q.ai_verdict = 'accept' AND q.ai_source = 'openrouter' "
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

"""Local orchestrator facade; recommendations and handoffs do not dispatch model tasks."""
from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from orchestration_models import DEFAULT_PREFERENCES, DEFAULT_PROFILES, UsageDemand, UsageBurnRateEstimator, epoch, iso, now_iso
from reset_intelligence import ResetSignalProvider, TiboPublicSignalProvider, OpenAIStatusSignalProvider, SOURCE_REGISTRY, calibrate_delivery
from execution_broker import OfficialCloudExecutionAdapter, capability, handoff_instruction
from planning_engine import PlanningEngine
from orchestration_verification import VerificationEngine
from window_state import bucket_id
from cloud_experiment import new_experiment, cloud_instruction, CloudExperimentVerifier
from planning_engine import TargetBoundaryPolicy
from recommendation_engine import RecommendationEngine, task_instruction
from autonomous_validation import autonomous_a4_result
from recommendation_lifecycle import RecommendationLifecycle, local_interval
from release_readiness import doctor, freshness
from ordinary_planner import OrdinaryPlanner
from reminder_delivery import tick as reminder_tick, acknowledge as reminder_ack, transition as reminder_transition
from file_persistence import replace_file
from usage_intelligence import select_quota_windows


class OrchestrationService:
    def __init__(self, data_dir: Path, *, mock=False):
        self.path = Path(data_dir)/"orchestration.json"
        self.mock = mock
        self.lock = threading.RLock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}
        for key in ("demands", "recommendations", "handoffs", "signals", "verifications", "history", "burnSamples", "cloudExperiments", "reminders"):
            if not isinstance(self.data.get(key), list): self.data[key] = []
        saved_prefs = self.data.get("preferences", {})
        self.data["preferences"] = {**copy.deepcopy(DEFAULT_PREFERENCES), **(saved_prefs if isinstance(saved_prefs, dict) else {})}
        if not isinstance(self.data.get("profiles"), list): self.data["profiles"] = copy.deepcopy(DEFAULT_PROFILES)
        self.demo = None
        self.lifecycle = list(self.data.get("lifecycle", []))[-100:]+[{"type": "AGENT_START", "timestamp": now_iso()}]
        self.signal_provider = ResetSignalProvider()
        self.data.setdefault('submissionReceipts',{})
        for demand in self.data['demands']:
            demand.setdefault('source','IMPORTED_LEGACY_DEMAND')
        self.recommendation_lifecycle = RecommendationLifecycle(self)

    def public_burn(self):
        return UsageBurnRateEstimator().estimate(self.data['burnSamples'], 'CODING')

    def save(self):
        self.data["lifecycle"] = copy.deepcopy(self.lifecycle[-100:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        replace_file(temp, self.path)

    def event(self, kind, details):
        self.data["history"].insert(0, {"type": kind, "timestamp": now_iso(), "details": details, "modelTaskSent": False})
        del self.data["history"][1000:]

    def capabilities(self, snapshot):
        # Availability is for proposal only; live runtime remains independently OFF.
        ready = self.mock and snapshot.get("app_server") in {"CONNECTED", "MOCK"}
        x = snapshot.get("execution", {})
        wake = x.get("wake", {}).get("taskScheduler")
        return [OfficialCloudExecutionAdapter().capability(),
                capability("WAKE_LOCAL", "UNKNOWN" if wake in {"AVAILABLE", "MOCK"} else "UNAVAILABLE", supportsScheduling=True,
                           reason="Wake support detected, actual sleep/resume reliability not verified."),
                capability("LOCAL_ONLY", "AVAILABLE" if ready else "UNAVAILABLE", supportsScheduling=True, supportsCancellation=True,
                           programmaticCreation=self.mock, reason="v0.9.1 is read-only cloud validation; real local dispatch is locked OFF. Mock proposals only."),
                capability("MANUAL_HANDOFF", "AVAILABLE", reason="Copy instructions; user schedules and confirms in official UI.")]

    def usage(self, snapshot, provider):
        windows = snapshot.get("windows", []) if snapshot.get("provider", {}).get("id") == provider else []
        primary, weekly = select_quota_windows(windows)
        bucket = bucket_id(primary) if primary else None
        current = {"bucketId": bucket, "windowDuration": primary.get("window_duration_minutes") if primary else None,
                   "remainingPercent": primary.get("remaining_percent") if primary else None,
                   "resetAt": primary.get("resets_at") if primary else None}
        current["quotaWindows"] = [
            {"bucketId": bucket_id(w), "windowDuration": w.get("window_duration_minutes"),
             "remainingPercent": w.get("remaining_percent"), "resetAt": w.get("resets_at")}
            for w in windows if isinstance(w, dict) and isinstance(w.get("window_duration_minutes"), int)
            and not isinstance(w.get("window_duration_minutes"), bool) and w["window_duration_minutes"] > 0
        ]
        state = {"state": primary.get("window_state", "INSUFFICIENT_DATA") if primary else "INSUFFICIENT_DATA"}
        weekly_quota = {"remainingPercent": weekly.get("remaining_percent") if weekly else None,
                        "resetAt": weekly.get("resets_at") if weekly else None}
        return current, state, weekly_quota

    def device(self, snapshot):
        return {"timezone": self.data["preferences"]["timezone"], "onlineHours": self.data["preferences"]["deviceOnlineHours"],
                "agentAvailable": snapshot.get("app_server") in {"CONNECTED", "MOCK"},
                "networkAvailable": snapshot.get("app_server") in {"CONNECTED", "MOCK"},
                "wakeVerified": False, "poweredOff": False}

    def demands(self):
        return copy.deepcopy(self.data["demands"])

    def generate(self, demand, snapshot):
        now = self.recommendation_lifecycle.clock()
        events = self.signal_provider.deduplicate(self.data["signals"], now)
        quota, window, weekly = self.usage(snapshot, demand["provider"])
        prefs = dict(self.data["preferences"])
        if demand.get("targetBoundaryPolicy"):
            prefs.update(targetBoundaryPolicy=demand["targetBoundaryPolicy"], earlyBoundaryMinutes=demand["boundaryMinutes"],
                         lateBoundaryMinutes=demand["boundaryMinutes"], customBoundaryMinutes=demand["boundaryMinutes"])
        if not prefs.get('researchMode'):
            return OrdinaryPlanner().recommend(demand,quota,window,weekly,self.public_burn(),prefs,now,freshness(snapshot,now)=='FRESH')
        return RecommendationEngine().recommend(demand, quota, window, weekly, self.signal_provider.forecast(events, now),
                    self.capabilities(snapshot), self.device(snapshot), prefs, now,
                    burn_rate=UsageBurnRateEstimator().estimate(self.data["burnSamples"], "CODING"))

    def run_demo(self):
        zone = ZoneInfo(self.data["preferences"]["timezone"])
        tomorrow = datetime.now(zone).replace(hour=9, minute=0, second=0, microsecond=0)+timedelta(days=1)
        demand = UsageDemand.from_payload({"title": "Tomorrow thesis coding · MOCK", "expectedStart": tomorrow.isoformat(),
                                         "expectedEnd": (tomorrow+timedelta(hours=3)).isoformat()}).public()
        simulated = capability("OFFICIAL_CLOUD", "AVAILABLE", supportsScheduling=True, simulated=True, creationMode="SIMULATED_HANDOFF")
        prefs = {**self.data["preferences"], "resetPlacement": "EARLY_BOUNDARY", "earlyBoundaryMinutes": 30, "preferredExecutionMethod": "AUTO"}
        forecast = self.signal_provider.forecast([], datetime.now(timezone.utc).timestamp())
        rec = PlanningEngine().recommend(demand, {"bucketId": "codex.primary.300", "windowDuration": 300, "remainingPercent": 100},
                                        {"state": "ROLLING"}, {"remainingPercent": 45}, forecast, [simulated], {}, prefs,
                                        datetime.now(timezone.utc).timestamp())
        self.demo = {"simulated": True, "demand": demand, "recommendation": rec, "forecast": forecast,
                     "weeklyRemaining": 45, "windowState": "ROLLING", "status": "AWAITING_USER_CONFIRMATION",
                     "modelTaskSent": False}
        return self.demo

    def public(self, snapshot):
        with self.lock:
            self.recommendation_lifecycle.tick(snapshot)
            reminder_tick(self,snapshot)
            now = self.recommendation_lifecycle.clock()
            result = copy.deepcopy(self.data)
            result["signals"] = self.signal_provider.deduplicate(self.data["signals"], now)
            observations = snapshot.get("window_state_observations", [])
            for signal in result["signals"]:
                effective = signal.get("effectiveTimeEstimate")
                if not effective: continue
                for identity in {bucket_id(o) for o in observations}:
                    series = sorted([{**o, "bucketId": bucket_id(o)} for o in observations if bucket_id(o) == identity], key=lambda o: epoch(o["observedAt"]))
                    before = [o for o in series if epoch(o["observedAt"])<=epoch(effective)]
                    after = [o for o in series if epoch(o["observedAt"])>=epoch(effective)]
                    if before and after:
                        delivery = calibrate_delivery(signal, before[-1], after[0], before[-1].get("resetAt"))
                        signal.setdefault("deliveryByBucket", {})[identity] = delivery
                deliveries = set(signal.get("deliveryByBucket", {}).values())
                signal["personalDelivery"] = "DELIVERED" if "DELIVERED" in deliveries else "NOT_OBSERVED" if deliveries == {"NOT_OBSERVED"} else "UNKNOWN"
            result["forecast"] = self.signal_provider.forecast(result["signals"], now)
            result["capabilities"] = self.capabilities(snapshot)
            result["sources"] = SOURCE_REGISTRY
            result["burnRate"] = UsageBurnRateEstimator().estimate(self.data["burnSamples"], "CODING")
            result["demo"] = copy.deepcopy(self.demo)
            result["creationMode"] = "MANUAL_HANDOFF_ONLY"
            result["liveExecutionEnabled"] = snapshot.get("execution", {}).get("liveExecutionEnabled", False)
            result["automaticExecution"] = False
            result["lifecycle"] = copy.deepcopy(self.lifecycle)
            result["providerSupport"] = "openai-codex currently implemented; other providers are contracts only"
            result["autonomousValidation"] = autonomous_a4_result()
            result['dataSchemaVersion']=15
            result['quotaFreshness']=freshness(snapshot)
            result['doctor']=doctor(snapshot,self.path.parent,self.data['preferences']['timezone'])
            result["researchMode"] = self.data["preferences"].get("researchMode") is True
            result["reminders"] = [{**r, "due": epoch(r["remindAt"]) <= now} for r in self.data["reminders"] if not r.get("dismissed")]
            self.recommendation_lifecycle.view(result)
            return result

    def handle(self, action, payload, snapshot):
        with self.lock:
            self.recommendation_lifecycle.tick(snapshot)
            if action == "demo":
                if not self.mock: raise ValueError("DEMO_REQUIRES_MOCK_MODE")
                return self.run_demo()
            if action == "demo-clear":
                self.demo = None; return {"cleared": True}
            if action == "demand":
                key=payload.get('submissionId')
                if key:
                    if not isinstance(key,str) or len(key)>80:raise ValueError('INVALID_SUBMISSION_ID')
                    import hashlib
                    fingerprint=hashlib.sha256(json.dumps({k:v for k,v in payload.items() if k!='submissionId'},sort_keys=True).encode()).hexdigest()
                    receipt=self.data['submissionReceipts'].get(key)
                    if receipt:
                        if receipt['fingerprint']!=fingerprint:raise ValueError('IDEMPOTENCY_KEY_REUSED')
                        did=receipt['demandId'];rid=self.recommendation_lifecycle.data['current'].get(did)
                        return {'demand':next(d for d in self.data['demands'] if d['id']==did),'recommendation':next(r for r in self.data['recommendations'] if r['id']==rid),'duplicateSubmission':True}
                if payload.get('date'):
                    start,end=local_interval(payload,self.data['preferences']['timezone'])
                    payload={**payload,'expectedStart':start,'expectedEnd':end}
                demand = UsageDemand.from_payload(payload).public()
                demand['originalTimezone']=self.data['preferences']['timezone']
                demand['source']='PRODUCT_PLANNER' if not self.data['preferences'].get('researchMode') else 'RESEARCH_PLANNER'
                if payload.get("targetBoundaryPolicy"):
                    demand["targetBoundaryPolicy"] = payload["targetBoundaryPolicy"]
                    demand["boundaryMinutes"] = float(payload.get("boundaryMinutes", 30))
                    TargetBoundaryPolicy.resolve(epoch(demand["expectedStart"]), epoch(demand["expectedEnd"]),
                        {"targetBoundaryPolicy": demand["targetBoundaryPolicy"], "earlyBoundaryMinutes": demand["boundaryMinutes"],
                         "lateBoundaryMinutes": demand["boundaryMinutes"], "customBoundaryMinutes": demand["boundaryMinutes"]})
                if epoch(demand["expectedStart"]) <= datetime.now(timezone.utc).timestamp():
                    raise ValueError("FUTURE_DEMAND_REQUIRED")
                rec = self.generate(demand, snapshot)
                self.data["demands"].insert(0, demand); self.data["recommendations"].insert(0, rec)
                self.recommendation_lifecycle.register(rec,self.recommendation_lifecycle.inputs(snapshot),self.recommendation_lifecycle.allocations(snapshot).get(demand['id']))
                self.event("DEMAND_PLANNED", {"demandId": demand["id"], "recommendationId": rec["id"]})
                if key:self.data['submissionReceipts'][key]={'fingerprint':fingerprint,'demandId':demand['id']}
                self.save(); return {"demand": demand, "recommendation": rec}
            if action == "recommend":
                demand = next((d for d in self.data["demands"] if d["id"] == payload.get("demandId")), None)
                if demand is None: raise ValueError("DEMAND_NOT_FOUND")
                previous=self.recommendation_lifecycle.data['current'].get(demand['id'])
                if previous:
                    record=self.recommendation_lifecycle.data['records'][previous]
                    if self.recommendation_lifecycle.clock()-record.get('lastRecomputedAt',0)<30:raise ValueError('RECOMPUTE_WAIT')
                    self.recommendation_lifecycle.state(previous,'RECOMPUTING','USER_REPLAN');self.recommendation_lifecycle.save()
                try:rec = self.generate(demand, snapshot)
                except Exception:
                    if previous:
                        self.recommendation_lifecycle.state(previous,'FAILED','RECOMPUTE_FAILED')
                        record.update(lastRecomputedAt=self.recommendation_lifecycle.clock(),pendingSince=self.recommendation_lifecycle.clock())
                        self.recommendation_lifecycle.save();self.save()
                    raise ValueError('RECOMPUTE_FAILED')
                self.data["recommendations"].insert(0, rec)
                self.recommendation_lifecycle.register(rec,self.recommendation_lifecycle.inputs(snapshot),self.recommendation_lifecycle.allocations(snapshot).get(demand['id']),previous)
                self.event("REPLANNED", {"recommendationId": rec["id"]}); self.save(); return rec
            if action == "preferences":
                prefs = {**self.data["preferences"], **{k: payload[k] for k in DEFAULT_PREFERENCES if k in payload}}
                if prefs["riskPreference"] not in {"Conservative", "Balanced", "Aggressive"}: raise ValueError("INVALID_RISK_PREFERENCE")
                if prefs["resetPlacement"] not in {"MIDPOINT", "EARLY_BOUNDARY"}: raise ValueError("INVALID_RESET_PLACEMENT")
                if prefs["preferredExecutionMethod"] not in {"AUTO", "OFFICIAL_CLOUD", "WAKE_LOCAL", "LOCAL_ONLY", "MANUAL_HANDOFF"}: raise ValueError("INVALID_BACKEND")
                if not 1 <= int(prefs["earlyBoundaryMinutes"]) <= 240: raise ValueError("INVALID_BOUNDARY_MINUTES")
                try: ZoneInfo(prefs["timezone"])
                except (KeyError, ValueError) as exc: raise ValueError("INVALID_TIMEZONE") from exc
                for k in ("workHours", "sleepHours", "deviceOnlineHours"):
                    if not isinstance(prefs[k], list) or len(prefs[k]) != 2: raise ValueError("INVALID_HOURS")
                    for value in prefs[k]:
                        try: datetime.strptime(value, "%H:%M")
                        except (TypeError, ValueError) as exc: raise ValueError("INVALID_HOURS") from exc
                prefs["publicSignalFetchEnabled"] = prefs["publicSignalFetchEnabled"] is True
                prefs["researchMode"] = prefs["researchMode"] is True
                buffer = float(prefs["safetyBufferMinutes"])
                if not 0 <= buffer <= 120: raise ValueError("INVALID_SAFETY_BUFFER")
                prefs["safetyBufferMinutes"] = buffer
                self.data["preferences"] = prefs
                self.event("PREFERENCES_SAVED", {}); self.save(); return prefs
            if action in {"task-instruction", "action-check"}:
                rec = next((r for r in self.data["recommendations"] if r["id"] == payload.get("recommendationId")), None)
                if not rec: raise ValueError("RECOMMENDATION_NOT_FOUND")
                self.recommendation_lifecycle.require(rec)
                if action=='action-check':return {'url':'https://chatgpt.com/codex','validUntil':self.recommendation_lifecycle.data['records'][rec['id']]['validUntil']}
                return {"instruction": task_instruction(rec), "validUntil":self.recommendation_lifecycle.data['records'][rec['id']]['validUntil'], "modelTaskSent": False, "officialCreatedByQuotaLab": False}
            if action == "reminder":
                rec = next((r for r in self.data["recommendations"] if r["id"] == payload.get("recommendationId")), None)
                if not rec:raise ValueError('NO_FUTURE_RECOMMENDATION')
                reminder_time=rec.get('reminderAt') if rec.get('planningPolicy')=='OFFICIAL_SNAPSHOT_ONLY' else rec.get('recommended_time')
                if freshness(snapshot,self.recommendation_lifecycle.clock())!='FRESH' and snapshot.get('last_snapshot'):raise ValueError('QUOTA_SNAPSHOT_UNAVAILABLE')
                if not reminder_time or not self.recommendation_lifecycle.reminder_allowed(rec):raise ValueError('RECOMMENDATION_INVALIDATED')
                now = self.recommendation_lifecycle.clock()
                if epoch(reminder_time)<=now:raise ValueError('RECOMMENDATION_EXPIRED')
                before=payload.get('beforeMinutes')
                if before is not None and before not in (5,10,30,60):raise ValueError('INVALID_REMINDER_OFFSET')
                existing = next((r for r in self.data["reminders"] if r["recommendationId"] == rec["id"] and not r.get("dismissed") and not r.get('invalidated')), None)
                if existing and existing.get('beforeMinutes')==before:return existing
                at=epoch(reminder_time)-before*60 if before is not None else min(now+600,epoch(reminder_time)-1)
                if at<=now:raise ValueError('REMINDER_TIME_PASSED')
                if existing:reminder_transition(self,existing,'CANCELLED','REPLACED_OFFSET',now)
                reminder = {"id": uuid.uuid4().hex, "recommendationId": rec["id"], "remindAt": iso(at),'beforeMinutes':before,
                            "createdAt": now_iso(), "delivery": "LOCAL_APP_ONLY", "modelTaskSent": False, "dismissed": False,
                            'demandId':rec['demandId'],'revision':self.recommendation_lifecycle.data['records'][rec['id']]['revision'],
                            'state':'SCHEDULED','stateReason':'USER_SCHEDULED','serverNow':iso(now),'createdAt':iso(now)}
                self.data["reminders"].insert(0, reminder); self.event("LOCAL_REMINDER_SAVED", {"id":reminder["id"],'demandId':rec['demandId'],
                    'recommendationId':rec['id'],'revision':reminder['revision'],'remindAt':reminder['remindAt'],'serverNow':iso(now)}); self.save(); return reminder
            if action=='reminder-displayed':return reminder_ack(self,payload,snapshot)
            if action == "reminder-dismiss":
                reminder = next((r for r in self.data["reminders"] if r["id"] == payload.get("id")), None)
                if not reminder: raise ValueError("REMINDER_NOT_FOUND")
                reminder["dismissed"] = True;reminder_transition(self,reminder,'CANCELLED','USER_DISMISSED',self.recommendation_lifecycle.clock()); self.save(); return reminder
            if action.startswith("cloud-"):
                if action == "cloud-create":
                    e = new_experiment(payload.get("experimentType", "OFFICIAL_CLOUD_CONTROLLED_TEST"), snapshot.get("window_state_observations", []))
                    self.data["cloudExperiments"].insert(0, e)
                else:
                    e = next((e for e in self.data["cloudExperiments"] if e["experimentId"] == payload.get("id")), None)
                    if e is None: raise ValueError("CLOUD_EXPERIMENT_NOT_FOUND")
                    e = copy.deepcopy(e)
                    if action == "cloud-handoff":
                        if payload.get("acknowledgeRisk") is not True: raise ValueError("HANDOFF_REQUIRES_ACKNOWLEDGEMENT")
                        rec = next((r for r in self.data["recommendations"] if r["id"] == payload.get("recommendationId")), None)
                        self.recommendation_lifecycle.require(rec)
                        if not rec or not rec.get("recommendedExecutionAt") or epoch(rec["recommendedExecutionAt"]) <= datetime.now(timezone.utc).timestamp():
                            raise ValueError("NO_FUTURE_RECOMMENDATION")
                        if rec.get("bucketId") != e["bucket"]: raise ValueError("CLOUD_BUCKET_MISMATCH")
                        if e["handoffId"]: return e
                        e.update(handoffId=uuid.uuid4().hex, generatedInstruction=cloud_instruction(rec),
                                 recommendedExecutionAt=rec["recommendedExecutionAt"], status="HANDOFF_GENERATED")
                    elif action == "cloud-scheduled":
                        if not e["handoffId"] or e["status"] != "HANDOFF_GENERATED" or payload.get("userConfirmed") is not True:
                            raise ValueError("CONFIRM_GENERATED_HANDOFF_FIRST")
                        e.update(officialScheduledAt=iso(payload["officialScheduledAt"]), status="USER_REPORTED_SCHEDULED")
                    elif action == "cloud-executed":
                        if e["status"] != "USER_REPORTED_SCHEDULED" or payload.get("userConfirmed") is not True:
                            raise ValueError("CONFIRM_SCHEDULED_TASK_FIRST")
                        run = iso(payload["officialRunAt"])
                        if epoch(run) > datetime.now(timezone.utc).timestamp() or epoch(run) < epoch(e["createdAt"]):
                            raise ValueError("INVALID_ACTUAL_RUN_TIME")
                        e.update(officialRunAt=run, officialRunTimeSource="USER_REPORTED", status="USER_REPORTED_EXECUTED")
                    elif action == "cloud-verify":
                        allowed = {"MULTIPLE_REQUESTS", "OTHER_KNOWN_REQUEST", "UNKNOWN_EXECUTION_TIME"}
                        reported = payload.get("confounders", [])
                        if not isinstance(reported, list) or any(x not in allowed for x in reported): raise ValueError("INVALID_CONFOUNDERS")
                        e["reportedConfounders"] = sorted(set(e.get("reportedConfounders", [])+reported))
                        if payload.get("userReportedNotRun") is True and e.get("officialRunAt"):
                            raise ValueError("CONFLICTING_RUN_REPORT")
                        e["userReportedNotRun"] = payload.get("userReportedNotRun") is True
                        e = CloudExperimentVerifier().verify(e, snapshot.get("window_state_observations", []),
                            lifecycle=self.lifecycle, requests=snapshot.get("scheduled_runs", []))
                    else: raise ValueError("UNKNOWN_CLOUD_ACTION")
                    self.data["cloudExperiments"] = [e if old["experimentId"] == e["experimentId"] else old for old in self.data["cloudExperiments"]]
                self.event("CLOUD_EXPERIMENT_RECORDED", {"experimentId": e["experimentId"], "status": e["status"], "classification": e["classification"]})
                self.save()
                response=copy.deepcopy(e)
                if action!='cloud-handoff':response['generatedInstruction']=None
                return response
            if action == "profile":
                name = str(payload.get("name", "")).strip()[:80]
                if not name: raise ValueError("PROFILE_NAME_REQUIRED")
                for key in ("start", "end"):
                    try: datetime.strptime(str(payload.get(key)), "%H:%M")
                    except ValueError as exc: raise ValueError("INVALID_HOURS") from exc
                if payload.get("intensity") not in {"LIGHT", "MEDIUM", "HEAVY"}: raise ValueError("INVALID_INTENSITY")
                profile = {"name": name, "start": payload["start"], "end": payload["end"], "intensity": payload["intensity"], "days": [1,2,3,4,5]}
                self.data["profiles"] = [p for p in self.data["profiles"] if p["name"] != name]+[profile]
                self.save(); return profile
            if action == "signal":
                signal = TiboPublicSignalProvider().normalize(payload)
                if not any(s["id"] == signal["id"] for s in self.data["signals"]): self.data["signals"].append(signal)
                self.event("PUBLIC_SIGNAL_IMPORTED", {"id": signal["id"]}); self.save(); return signal
            if action == "signals-fetch":
                if not self.data["preferences"]["publicSignalFetchEnabled"]: raise ValueError("PUBLIC_FETCH_REQUIRES_OPT_IN")
                try: fetched = OpenAIStatusSignalProvider().fetch()
                except Exception as exc: raise ValueError("PUBLIC_FETCH_FAILED; retained existing evidence") from exc
                for s in fetched:
                    if not any(old["id"] == s["id"] for old in self.data["signals"]): self.data["signals"].append(s)
                self.event("PUBLIC_STATUS_FETCHED", {"count": len(fetched)}); self.save(); return {"count": len(fetched), "modelTaskSent": False}
            if action == "handoff":
                rec = next((r for r in self.data["recommendations"] if r["id"] == payload.get("recommendationId")), None)
                self.recommendation_lifecycle.require(rec)
                if rec is None or not rec.get("recommendedExecutionAt"): raise ValueError("NO_EXECUTABLE_RECOMMENDATION")
                if epoch(rec["recommendedExecutionAt"]) <= datetime.now(timezone.utc).timestamp(): raise ValueError("RECOMMENDATION_EXPIRED")
                if payload.get("acknowledgeRisk") is not True: raise ValueError("HANDOFF_REQUIRES_ACKNOWLEDGEMENT")
                existing = next((h for h in self.data["handoffs"] if h["recommendationId"] == rec["id"]), None)
                if existing: return existing
                demand = next(d for d in self.data["demands"] if d["id"] == rec["demandId"])
                h = {"id": uuid.uuid4().hex, "recommendationId": rec["id"], "provider": rec["provider"],
                     "executionBackend": rec["backend"],
                     "targetExecutionAt": rec["recommendedExecutionAt"], "generatedInstruction": handoff_instruction(rec,demand),
                     "status": "AWAITING_USER_CONFIRMATION", "createdAt": now_iso(), "userConfirmedScheduledAt": None,
                     "officialTaskReference": None, "verifiedRunAt": None, "verificationStatus": "INCONCLUSIVE",
                     "officialCreatedByQuotaLab": False}
                self.data["handoffs"].insert(0,h); self.event("HANDOFF_PREPARED", {"id": h["id"]}); self.save(); return h
            if action in {"handoff-confirm", "handoff-cancel", "verify"}:
                h = next((h for h in self.data["handoffs"] if h["id"] == payload.get("id")), None)
                if h is None: raise ValueError("HANDOFF_NOT_FOUND")
                if action == "handoff-confirm":
                    self.recommendation_lifecycle.require(next((r for r in self.data['recommendations'] if r['id']==h['recommendationId']),None))
                    if payload.get("userConfirmed") is not True: raise ValueError("USER_CONFIRMATION_REQUIRED")
                    h.update(status="USER_REPORTED_SCHEDULED", userConfirmedScheduledAt=now_iso(), officialTaskReference=str(payload.get("officialTaskReference", ""))[:500])
                elif action == "handoff-cancel":
                    h["status"] = "CANCELLED_LOCALLY_NOT_OFFICIALLY"
                else:
                    rec = next(r for r in self.data["recommendations"] if r["id"] == h["recommendationId"])
                    observations = [{**o, "bucketId": bucket_id(o)} for o in snapshot.get("window_state_observations", [])]
                    run = {"observedRunAt": iso(payload["observedRunAt"]), "provenance": "USER_REPORTED"} if payload.get("observedRunAt") else {}
                    v = VerificationEngine().verify(rec, observations, run=run, lifecycle_events=self.lifecycle)
                    self.data["verifications"].insert(0,v); h["verificationStatus"] = v["verificationConfidence"]
                    self.event("VERIFICATION_RECORDED", {"id": h["id"], "confidence": v["verificationConfidence"]}); self.save(); return v
                self.event("HANDOFF_UPDATED", {"id": h["id"], "status": h["status"]}); self.save()
                response=copy.deepcopy(h);response['generatedInstruction']=None;return response
            raise ValueError("UNKNOWN_ORCHESTRATION_ACTION")

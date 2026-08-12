# PHASE 2 BACKLOG — PRODUCTION HARDENING

**Status:** Functionally Implemented, Needs Production Hardening

**Date:** June 29, 2026

**Priority:** 🔴 CRITICAL

---

## OVERVIEW

✅ **Done:** Core APIs, endpoints, basic functionality, tests passing

⏳ **Remaining:** Convert mock services → production services

**Effort:** High quality hardening (not quick hacks)

**Timeline:** No estimates, focus on quality

---

## BACKLOG (Prioritized by Dependency)

### TIER 1: Foundation (Must Complete First)

These are dependencies for other services. Do these first.

---

#### **TASK 1.1: Business Brain Production Hardening**

**Current State:** Mock/prototype service

**Problem:**

- No tenant scoping (all workspaces see same data)
- Queries not optimized
- No caching
- No error handling
- Hard to maintain business rules

**What Needs to Happen:**

- Validate all input parameters
- Add cache integration for expensive queries
- Enforce workspace filtering on every query
- Add try/catch around all DB interactions
- Add structured logging for debug/info/error
- Ensure proper query indexes exist
- Expand unit tests to edge cases and tenant isolation
- Update documentation

**Checklist:**

- [ ] All queries have `workspace_id` filter
- [ ] Input validation on all methods
- [ ] Cache integration for all expensive queries
- [ ] Error handling (try/catch on all DB calls)
- [ ] Logging (debug, info, error levels)
- [ ] Query optimization (proper indexes)
- [ ] Unit tests cover edge cases
- [ ] Documentation updated

**Estimated Effort:** 3-4 days

**Owner:** CP

**Blocker For:** Rules Engine, Workflow Engine, AI Framework

**Definition of Done:**

- ✅ All business brain calculations production-ready
- ✅ Tenant isolation tested and verified
- ✅ Expensive queries cached
- ✅ 95%+ test coverage
- ✅ <1 second response time (cached results)
- ✅ Error handling comprehensive
- ✅ Documentation complete

---

#### **TASK 1.2: Rules Engine — DB-Driven Rules**

**Current State:** Hardcoded rules in Python

**Problem:**

- Credit limits and thresholds are hardcoded
- Rules cannot be customized per workspace
- Business logic is not configurable

**What Needs to Happen:**

- Create `business_rules` table for workspace-specific rule config
- Load rule thresholds from the database
- Remove hardcoded thresholds from Python
- Evaluate rules against DB-sourced limits and party data
- Cache rules and invalidate on update

**Checklist:**

- [ ] `business_rules` table created
- [ ] Rules loaded from database, not hardcoded
- [ ] Credit limit sourced from party config
- [ ] Minimum stock sourced from inventory config
- [ ] All hardcoded thresholds moved to database
- [ ] Rules cache invalidation on update
- [ ] Tests cover multiple workspaces (different rules)
- [ ] Documentation updated

**Estimated Effort:** 2-3 days

**Owner:** CP

**Blocker For:** Workflow Engine

**Definition of Done:**

- ✅ Zero hardcoded business rules in code
- ✅ All rules in database
- ✅ Rules configurable per workspace
- ✅ Rule evaluation tested
- ✅ Credit limits working correctly
- ✅ Minimum stock working correctly
- ✅ Tests passing
- ✅ Documentation complete

---

### TIER 2: Real Persistence (Second Priority)

These depend on Tier 1 being stable.

---

#### **TASK 2.1: Event Engine — Real Persistence**

**Current State:** Events are in-memory only, lost on restart

**Problem:**

- Events are lost on crash/restart
- No audit trail
- No replay capability

**What Needs to Happen:**

- Persist events to a database table
- Add event history query support
- Publish events to a message queue for real-time delivery
- Support replay and recovery

**Checklist:**

- [x] `events` table created with indexes
- [x] `event_subscriptions` table created
- [x] Events persisted to database
- [x] Events published to message queue for real-time delivery when Redis is configured
- [x] Event history queryable
- [x] Subscribers notified in real-time
- [x] Event replay working
- [x] Tests cover persistence + real-time
- [x] Documentation updated

**Estimated Effort:** 2-3 days

**Owner:** CP

**Depends On:** Task 1.1

**Definition of Done:**

- ✅ All events persisted to database
- ✅ Event stream working in real-time
- ✅ Event history available
- ✅ Event replay working
- ✅ Tests passing
- ✅ Documentation complete

---

#### **TASK 2.2: Workflow Engine — Real State Persistence**

**Current State:** Workflow state in memory, not persistent

**Problem:**

- Workflow state lost on restart
- No execution audit trail
- No recoverability

**What Needs to Happen:**

- Persist workflow definitions and executions
- Log step-by-step workflow progress
- Support resume/recovery after restart
- Integrate with rules and events

**Checklist:**

- [x] `workflows` table created
- [x] `workflow_executions` table created
- [x] `workflow_step_executions` table created
- [x] Workflow state persisted to database
- [x] State recoverable on server restart
- [x] Execution status queryable
- [x] Step-by-step execution logged
- [x] Pause/resume working
- [x] Tests cover persistence + recovery
- [x] Documentation updated

**Estimated Effort:** 2-3 days

**Owner:** CP

**Depends On:** Task 1.1, Task 1.2

**Definition of Done:**

- ✅ All workflow executions persisted
- ✅ State recoverable on restart
- ✅ Status queryable
- ✅ Tests passing
- ✅ Documentation complete

---

#### **TASK 2.3: Cache Manager — Real Caching Logic**

**Current State:** Cache is mock/in-memory

**Problem:**

- Cache lost on restart
- No TTL
- No invalidation strategy

**What Needs to Happen:**

- Implement Redis-based cache
- Add TTL support
- Add pattern invalidation
- Integrate event-driven cache invalidation

**Checklist:**

- [x] Redis connection configured (when `REDIS_URL`/`CACHE_URL` is set)
- [x] Real `get()` implementation working
- [x] Real `set()` implementation with TTL working
- [x] `delete()` working
- [x] Pattern invalidation working
- [x] Invalidations on invoice/create and stock changes
- [x] Tests cover cache hits/misses
- [x] Documentation updated

**Estimated Effort:** 1-2 days

**Owner:** CP

**Depends On:** Task 2.1

**Definition of Done:**

- ✅ All expensive queries cached
- ✅ Redis integration working
- ✅ Cache invalidation working
- ✅ Tests passing
- ✅ Documentation complete

---

### TIER 3: Advanced Features (Lower Priority)

---

#### **TASK 3.1: AI Decision Framework**

**Dependencies:** Task 1.1, Task 1.2

**Effort:** 2-3 days

**What to Do:**

- Implement decision flow through Business Brain → Rules → KG → Memory
- Add cost tracking for AI inference
- Add optimization suggestions
- Log AI decisions for audit

---

#### **TASK 3.2: Knowledge Graph Production Hardening**

**Dependencies:** Task 2.1

**Effort:** 3-4 days

**What to Do:**

- Persist knowledge graph data
- Auto-discover relationships from events
- Optimize entity lookup and path finding
- Add relationship confidence scoring

---

## IMPLEMENTATION ORDER

**Week 1:**

- Monday: Task 1.1
- Tuesday: Task 1.1
- Wednesday: Task 1.2
- Thursday: Task 1.2
- Friday: Task 2.1

**Week 2:**

- Monday: Task 2.1
- Tuesday: Task 2.2
- Wednesday: Task 2.2
- Thursday: Task 2.3
- Friday: Task 2.3

**Week 3:**

- Monday: Task 3.1
- Tuesday: Task 3.2
- Wednesday–Friday: Testing, hardening, documentation

---

## QUALITY GATES

Before marking done:

- No hardcoded values
- Tenant scoping on all queries
- Comprehensive error handling
- 95%+ test coverage
- Documentation updated
- No secrets in logs
- Authorization checks present

---

## SUCCESS CRITERIA

When all tasks are complete:

- ✅ Business Brain is production-ready
- ✅ Rules Engine is configurable per workspace
- ✅ Event Engine persists events
- ✅ Workflow Engine state is persistent and recoverable
- ✅ Cache Manager is real and robust
- ✅ AI Framework is cost-aware
- ✅ Knowledge Graph is persistent and auto-updating

**Result:** Phase 2 ready for production deployment.

---

**Prepared by:** Chief Engineer (Claude)

**Date:** June 29, 2026

**Status:** Ready for execution

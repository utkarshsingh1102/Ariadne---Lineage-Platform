parser grammar TWSComposerParser;

options { tokenVocab = TWSComposerLexer; }

// ============================================================================
// Entry rule
//
// v0.2 — the composer language has 6 top-level constructs, not just SCHEDULE.
// The v0.1 grammar accepted ``scheduleDefinition* EOF`` only, which caused a
// real-world composer file with workstation/calendar/resource/prompt/event-rule
// blocks to fail the parse — but the failure was silently swallowed by
// CollectingErrorListener (Phase 0 finding). Phase 2 makes the grammar match
// the language; Phase 1 surfaced the failure.
// ============================================================================
compilationUnit
    : ( scheduleDefinition
      | workstationDefinition
      | calendarDefinition
      | resourceDefinition
      | promptDefinition
      | eventRuleDefinition
      )* EOF
    ;

// ============================================================================
// Workstation (CPUNAME ... END)
// ============================================================================
workstationDefinition
    : CPUNAME parserId workstationProperty* END
    ;

workstationProperty
    : DESCRIPTION STRING
    | OS (UNIX | WINDOWS | parserId)
    | NODE hostName (TCPADDR INT)?
    | FOR MAESTRO workstationMaestroBlock
    | DOMAIN parserId
    | TYPE (FTA | MANAGER | parserId)
    | AUTOLINK (ON | OFF)
    | BEHINDFIREWALL (ON | OFF)
    ;

// Hostnames are dotted FQDNs: ``etl01.bank.internal``.
hostName
    : parserId ( DOT parserId )*
    ;

// MAESTRO sub-block — TYPE/AUTOLINK/BEHINDFIREWALL also accepted at the outer
// workstationProperty level so the grammar is forgiving of layout variations
// in real-world files. ``FOR MAESTRO`` is parsed as a marker; the indented
// properties that follow are recognised by the same workstationProperty rule.
workstationMaestroBlock
    : workstationProperty*
    ;

// ============================================================================
// Calendar (CALENDAR <name> [<description>] <date>...)
// No END terminator in real TWS; the calendar's date list ends when the next
// top-level construct begins.
// ============================================================================
calendarDefinition
    : CALENDAR parserId STRING? dateLiteral*
    ;

// ============================================================================
// Resource (RESOURCE <qualifiedName> <quantity> [<description>])
// ============================================================================
resourceDefinition
    : RESOURCE qualifiedName INT STRING?
    ;

// ============================================================================
// Prompt (PROMPT <name> "<text>")
// ============================================================================
promptDefinition
    : PROMPT parserId STRING
    ;

// ============================================================================
// Event rule (EVENTRULE <name> ... END)
// ============================================================================
eventRuleDefinition
    : EVENTRULE parserId eventRuleProperty* END
    ;

eventRuleProperty
    : DESCRIPTION STRING
    | IS ACTIVE
    | EVENTRULETYPE parserId
    | EVENT parserId eventBody
    | ACTION parserId actionBody
    ;

eventBody
    : (NODE hostName)?
      (FILENAME STRING)?
    ;

actionBody
    : (JOBSTREAM qualifiedName)?
    ;

// ============================================================================
// Schedule
// ============================================================================
scheduleDefinition
    : scheduleHeader scheduleProperty* COLON jobDefinition* END
    ;

scheduleHeader
    : SCHEDULE qualifiedName
    ;

scheduleProperty
    : descriptionClause
    | onClause
    | exceptRunCycleClause
    | atClause
    | untilClause
    | deadlineClause
    | carryForwardClause
    | followsClause
    | priorityClause
    | limitClause
    | validFromClause
    | validToClause
    ;

descriptionClause   : DESCRIPTION STRING ;

// v0.2 — ON RUNCYCLE optionally followed by VALIDFROM, a CALENDAR reference,
// AND/OR an RRULE-style string literal. All three modifiers are common.
onClause
    : ON RUNCYCLE runCyclePhrase
      ( VALIDFROM dateLiteral )?
      ( CALENDAR parserId )?
      STRING?
    ;

// Multi-token run-cycle name (e.g. `MONTHLY ON LAST WORKDAY`,
// `DAILY EVERY HOUR`, `WEEKDAY EXCEPT HOLIDAYS`). Greedy — stops at the next
// distinct keyword token (VALIDFROM/VALIDTO/AT/UNTIL/CALENDAR/STRING/…).
runCyclePhrase
    : ( ID | INT | ON )+
    ;

exceptRunCycleClause
    : EXCEPT RUNCYCLE runCyclePhrase STRING?
    ;

atClause            : AT timeLiteral ;
// v0.2 — UNTIL gains an optional ONUNTIL CANC tail.
untilClause         : UNTIL timeLiteral ( ONUNTIL CANC )? ;
deadlineClause      : DEADLINE timeLiteral ;
carryForwardClause  : CARRYFORWARD ;
priorityClause      : PRIORITY INT ;
limitClause         : LIMIT INT ;
validFromClause     : VALIDFROM dateLiteral ;
validToClause       : VALIDTO dateLiteral ;

// v0.2 — FOLLOWS now captures an optional IF SUCC | IF ABEND | IF RC=N
// condition per target. Two predecessors on the same parent with different
// conditions (RC=0 vs RC=4) must produce TWO distinct edges — that's
// enforced in Phase 5 via condition being part of the MERGE key.
followsClause
    : FOLLOWS followsItem ( COMMA followsItem )*
    ;

followsItem
    : dependencyTarget ( IF followsCondition )?
    ;

followsCondition
    : SUCC
    | ABEND
    | RC EQ INT
    ;

// ============================================================================
// Job
// ============================================================================
jobDefinition
    : jobName jobProperty+
    ;

jobName
    : qualifiedName
    | parserId
    ;

jobProperty
    : scriptNameClause
    | streamLogonClause
    | descriptionClause
    | recoveryClause
    | followsClause
    | needsClause
    | opensClause
    | atClause
    | everyClause
    | promptDepClause
    | priorityClause
    ;

scriptNameClause     : SCRIPTNAME scriptPath ;
streamLogonClause    : STREAMLOGON parserId ;

// v0.2 — RECOVERY may name a recovery JOB to run on failure:
//   ``RECOVERY RERUN AFTER ETL_AGENT_01#CLEANUP_LANDING``
recoveryClause
    : RECOVERY recoveryAction ( AFTER dependencyTarget )?
    ;
recoveryAction       : STOP | RERUN | CONTINUE ;

// NEEDS — optional qualifying workstation; ``NEEDS 1 ETL_AGENT_01#DB_CONN_POOL``
needsClause          : NEEDS INT qualifiedName ;
opensClause          : OPENS opensTarget ;

// OPENS targets can be a simple path or a workstation-qualified path:
//   ``OPENS "/data/landing/orders_ready.flag"``
//   ``OPENS ETL_AGENT_01#"/data/landing/orders_ready.flag"``
opensTarget
    : ( parserId HASH )? scriptPath
    ;

// v0.2 — EVERY rerun cadence + PROMPT manual gate.
everyClause          : EVERY INT ;
promptDepClause      : PROMPT parserId ;

scriptPath
    : STRING
    | PATH
    ;

// ============================================================================
// Common bits
// ============================================================================

// Job/schedule dependency target. Handles four shapes:
//   bare:           `JOB_NAME`
//   2-part:         `WORKSTATION#STREAM`           (schedule-level follows)
//   2-part qualified job: `WORKSTATION#STREAM.JOB`
//   wildcard:       `WORKSTATION#STREAM.@`
//   3-part legacy:  `WORKSTATION#SCHEDULER#NAME` (or `.@`)
// Phase 4 inspects the parse tree to classify internal vs external.
dependencyTarget
    : qualifiedName ( DOT (AT_SIGN | parserId) )?
    ;

qualifiedName
    : parserId ( HASH parserId )* ( DOT parserId )*
    ;

// v0.2 — ``parserId`` is the safety net for keyword-as-identifier collisions.
// Anywhere a user-defined name is expected (workstation names, job names,
// resource names, etc.) we accept the generic ID rule OR any keyword that
// might plausibly appear as a name. Without this, a workstation literally
// named ``ON`` or a job named ``EVENT`` would break the parse.
parserId
    : ID
    | ON
    | TYPE
    | FOR
    | EVENT
    | NODE
    | ACTION
    | OS
    | RC
    | UNIX
    | WINDOWS
    | DOMAIN
    | MANAGER
    | FTA
    | SBS
    | IS
    | ACTIVE
    | OFF
    ;

dateLiteral
    : DATE
    | INT   // bare-year shortcut, rare
    | parserId  // some calendars use named tokens like "EVERY_WEEKDAY"
    ;

timeLiteral
    : INT   // composer typically writes times as `0530`, `0900`, etc.
    ;

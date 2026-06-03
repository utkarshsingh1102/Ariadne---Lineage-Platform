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
    | notOnClause            // v0.3 — NOTON RUNCYCLE <name> "rule"
    | exceptRunCycleClause
    | atClause
    | untilClause
    | deadlineClause
    | carryForwardClause
    | followsClause
    | notFollowsClause       // v0.3 — NOTFOLLOWS <ref> (mutual exclusion)
    | matchingClause         // v0.3 — MATCHING PREVIOUS
    | needsClause            // v0.3 — NEEDS at schedule level (was job-only)
    | vartableClause         // v0.3 — VARTABLE <name>
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

// v0.3 — NOTON RUNCYCLE is the inverse: "do not run on the rule's matches".
// Common form in real composer files for holiday calendars:
//   NOTON RUNCYCLE HOLIDAYS "CALENDAR=CORP_HOLIDAYS"
notOnClause
    : NOTON RUNCYCLE runCyclePhrase
      ( CALENDAR parserId )?
      STRING?
    ;

// v0.3 — MATCHING PREVIOUS (carry-forward matching criterion).
matchingClause
    : MATCHING PREVIOUS
    ;

// v0.3 — VARTABLE <name>. Names a variable table whose ^VAR^ tokens are
// resolved at runtime; we just capture the reference.
vartableClause
    : VARTABLE parserId
    ;

// v0.3 — NOTFOLLOWS at schedule level: mutual exclusion.
//   NOTFOLLOWS AUDIT_SRV#AD_HOC_AUDIT_STREAM.@
notFollowsClause
    : NOTFOLLOWS dependencyTarget
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
// v0.3 — DEADLINE may carry an ONUNTIL action: ``DEADLINE 0600 ONUNTIL SUPPR``
// means "if the deadline passes, suppress this job"; ``CANC`` cancels it.
deadlineClause
    : DEADLINE timeLiteral ( ONUNTIL ( SUPPR | CANC ) )?
    ;
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
    : dependencyTarget ( IF? followsCondition )?
    ;

// v0.3 — SUCCESS and ABEND can appear bare (without the IF keyword) in real
// composer files: ``FOLLOWS STAGING_VALIDATION SUCCESS, DB_RECOVER_WARN``.
followsCondition
    : SUCC
    | SUCCESS
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
    | untilClause            // v0.3 — job-level UNTIL for loop blocks
    | deadlineClause         // v0.3 — job-level DEADLINE (was schedule-only)
    | onConditionClause      // v0.3 — ON <jobname> RC=N or VAL <expr>
    | promptDepClause
    | priorityClause
    ;

// v0.3 — Conditional routing on return code (ON <jobname> RC=N) or a more
// complex VAL expression with comparison operators (RC>=1 AND RC<=5).
// The named target is another job in the same schedule that runs when the
// condition matches. This becomes a DEPENDS_ON_RC edge in the IR.
onConditionClause
    : ON parserId ( RC EQ INT | VAL valExpression )
    ;

valExpression
    : valComparison ( ( AND | OR ) valComparison )*
    ;

valComparison
    : RC ( EQ | GE | LE | GT | LT ) INT
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

// v0.3 — PROMPT at job level can also be an inline text literal
// (``PROMPT "Schema mismatch — proceed?"``), not just a named prompt ref.
promptDepClause
    : PROMPT ( parserId | STRING )
    ;

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

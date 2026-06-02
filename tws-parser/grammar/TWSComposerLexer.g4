lexer grammar TWSComposerLexer;

// ----------------------------------------------------------------------------
// Keywords (case-insensitive in real composer files)
//
// All keyword rules must come BEFORE the generic ID rule so the maximal-munch
// lexer picks the keyword for exact matches and ID for longer extensions
// (e.g. `EVENT_TRIGGER` is ID, not EVENT followed by `_TRIGGER`).
// ----------------------------------------------------------------------------

// Existing v0.1 keywords
SCHEDULE     : [Ss][Cc][Hh][Ee][Dd][Uu][Ll][Ee];
END          : [Ee][Nn][Dd];
RUNCYCLE     : [Rr][Uu][Nn][Cc][Yy][Cc][Ll][Ee];
VALIDFROM    : [Vv][Aa][Ll][Ii][Dd][Ff][Rr][Oo][Mm];
VALIDTO      : [Vv][Aa][Ll][Ii][Dd][Tt][Oo];
AT           : [Aa][Tt];

// ONUNTIL must precede UNTIL/ON so ONUNTIL is preferred for `ONUNTIL CANC`.
ONUNTIL      : [Oo][Nn][Uu][Nn][Tt][Ii][Ll];
ON           : [Oo][Nn];
UNTIL        : [Uu][Nn][Tt][Ii][Ll];

CARRYFORWARD : [Cc][Aa][Rr][Rr][Yy][Ff][Oo][Rr][Ww][Aa][Rr][Dd];
FOLLOWS      : [Ff][Oo][Ll][Ll][Oo][Ww][Ss];
PRIORITY     : [Pp][Rr][Ii][Oo][Rr][Ii][Tt][Yy];
SCRIPTNAME   : [Ss][Cc][Rr][Ii][Pp][Tt][Nn][Aa][Mm][Ee];
STREAMLOGON  : [Ss][Tt][Rr][Ee][Aa][Mm][Ll][Oo][Gg][Oo][Nn];
DESCRIPTION  : [Dd][Ee][Ss][Cc][Rr][Ii][Pp][Tt][Ii][Oo][Nn];
RECOVERY     : [Rr][Ee][Cc][Oo][Vv][Ee][Rr][Yy];
NEEDS        : [Nn][Ee][Ee][Dd][Ss];
OPENS        : [Oo][Pp][Ee][Nn][Ss];
STOP         : [Ss][Tt][Oo][Pp];
RERUN        : [Rr][Ee][Rr][Uu][Nn];
CONTINUE     : [Cc][Oo][Nn][Tt][Ii][Nn][Uu][Ee];

// v0.2 — top-level objects
CPUNAME      : [Cc][Pp][Uu][Nn][Aa][Mm][Ee];
CALENDAR     : [Cc][Aa][Ll][Ee][Nn][Dd][Aa][Rr];
RESOURCE     : [Rr][Ee][Ss][Oo][Uu][Rr][Cc][Ee];
PROMPT       : [Pp][Rr][Oo][Mm][Pp][Tt];

// EVENTRULETYPE must precede EVENTRULE which must precede EVENT
// so the longest token wins for each spelling.
EVENTRULETYPE: [Ee][Vv][Ee][Nn][Tt][Rr][Uu][Ll][Ee][Tt][Yy][Pp][Ee];
EVENTRULE    : [Ee][Vv][Ee][Nn][Tt][Rr][Uu][Ll][Ee];
EVENT        : [Ee][Vv][Ee][Nn][Tt];

// v0.2 — conditions / modifiers
IF           : [Ii][Ff];
SUCC         : [Ss][Uu][Cc][Cc];
ABEND        : [Aa][Bb][Ee][Nn][Dd];
RC           : [Rr][Cc];
EVERY        : [Ee][Vv][Ee][Rr][Yy];
DEADLINE     : [Dd][Ee][Aa][Dd][Ll][Ii][Nn][Ee];
LIMIT        : [Ll][Ii][Mm][Ii][Tt];
EXCEPT       : [Ee][Xx][Cc][Ee][Pp][Tt];
CANC         : [Cc][Aa][Nn][Cc];
AFTER        : [Aa][Ff][Tt][Ee][Rr];

// v0.2 — workstation properties
// AUTOLINK / BEHINDFIREWALL must precede their longer-prefix candidates if any;
// they don't share prefixes with other keywords so order with the rest.
NODE         : [Nn][Oo][Dd][Ee];
TCPADDR      : [Tt][Cc][Pp][Aa][Dd][Dd][Rr];
FOR          : [Ff][Oo][Rr];
MAESTRO      : [Mm][Aa][Ee][Ss][Tt][Rr][Oo];
TYPE         : [Tt][Yy][Pp][Ee];
FTA          : [Ff][Tt][Aa];
MANAGER      : [Mm][Aa][Nn][Aa][Gg][Ee][Rr];
OS           : [Oo][Ss];
UNIX         : [Uu][Nn][Ii][Xx];
WINDOWS      : [Ww][Ii][Nn][Dd][Oo][Ww][Ss];
AUTOLINK     : [Aa][Uu][Tt][Oo][Ll][Ii][Nn][Kk];
BEHINDFIREWALL : [Bb][Ee][Hh][Ii][Nn][Dd][Ff][Ii][Rr][Ee][Ww][Aa][Ll][Ll];
OFF          : [Oo][Ff][Ff];
DOMAIN       : [Dd][Oo][Mm][Aa][Ii][Nn];

// v0.2 — event-rule keywords
IS           : [Ii][Ss];
ACTIVE       : [Aa][Cc][Tt][Ii][Vv][Ee];
FILENAME     : [Ff][Ii][Ll][Ee][Nn][Aa][Mm][Ee];
ACTION       : [Aa][Cc][Tt][Ii][Oo][Nn];
SBS          : [Ss][Bb][Ss];
JOBSTREAM    : [Jj][Oo][Bb][Ss][Tt][Rr][Ee][Aa][Mm];

// ----------------------------------------------------------------------------
// LINE_COMMENT — must come BEFORE HASH so the lexer prefers it on length ties.
//
// For a standalone ``#`` at column 0 followed only by newline (a blank banner
// line in the fixture header), both LINE_COMMENT and HASH match exactly one
// character. ANTLR breaks length ties via rule-declaration order, so
// LINE_COMMENT must precede HASH or that ``#`` becomes a HASH token and
// reaches the parser. Predicate: fire when ``#`` is at column 0 OR after
// whitespace. ``#`` between identifier chars (WS#STREAM) falls through to HASH.
// ----------------------------------------------------------------------------
LINE_COMMENT
    : { self.column == 0 or self._input.LA(-1) in (32, 9) }? '#' ~[\r\n]* -> channel(HIDDEN)
    ;

// ----------------------------------------------------------------------------
// Punctuation
// ----------------------------------------------------------------------------
COLON       : ':';
SEMI        : ';';
HASH        : '#';
DOT         : '.';
AT_SIGN     : '@';
COMMA       : ',';
LBRACE      : '{';
RBRACE      : '}';
EQ          : '=';

// ----------------------------------------------------------------------------
// Literals
// ----------------------------------------------------------------------------
// Composer-style date `MM/DD/YYYY` (or YYYY/MM/DD — we don't enforce semantics).
// Must precede INT (longest match) and PATH (which would otherwise consume the
// `/MM/YYYY` tail after the first integer).
DATE        : [0-9]+ '/' [0-9]+ '/' [0-9]+;
INT         : [0-9]+;

// Quoted string ("...") with optional doubled quotes inside ("" -> ").
STRING
    : '"' ( '""' | ~["\r\n] )* '"'
    ;

// Bare identifier: letters, digits, underscore. Must start with a letter or _.
ID
    : [A-Za-z_] [A-Za-z_0-9]*
    ;

// Unix-style file path (starts with /, lets us recognise SCRIPTNAME bare paths).
PATH
    : '/' [A-Za-z0-9_./\-]+
    ;

// ----------------------------------------------------------------------------
// Block comments + whitespace
//
// LINE_COMMENT moved up above HASH (see header); see that rule for the
// column-0/after-whitespace predicate that distinguishes comments from the
// HASH used in ``WORKSTATION#STREAM`` qualified names.
// ----------------------------------------------------------------------------

BLOCK_COMMENT
    : '/*' .*? '*/' -> channel(HIDDEN)
    ;

CONTINUATION
    : '\\' [\r\n]+ -> channel(HIDDEN)
    ;

WS  : [ \t\r\n]+ -> channel(HIDDEN) ;

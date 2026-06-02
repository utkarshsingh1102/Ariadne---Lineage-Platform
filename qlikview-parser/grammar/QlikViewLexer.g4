lexer grammar QlikViewLexer;

// ---------------------------------------------------------------------------
// QlikView load-script lexer
//
// Pre-processing (encoding detection, $(Include=...) inlining, $(varName)
// macro expansion) is done in Python BEFORE the lexer runs — by the time the
// lexer sees the input there are no `$(...)` references and no INCLUDE chain.
// ---------------------------------------------------------------------------

// Skip whitespace and all comment flavours
WS              : [ \t\r\n]+         -> channel(HIDDEN) ;
LINE_COMMENT    : '//' ~[\r\n]*      -> channel(HIDDEN) ;
BLOCK_COMMENT   : '/*' .*? '*/'      -> channel(HIDDEN) ;
REM_COMMENT     : [Rr][Ee][Mm] ~[\r\n;]* ';'? -> channel(HIDDEN) ;

// SQL block — captured as a single chunk; sqlglot parses the body downstream.
// Trigger: the literal token "SQL " at statement start; consume everything up
// to the next unquoted ';'.
SQL_BLOCK       : [Ss][Qq][Ll] [ \t\r\n]+ [Ss][Ee][Ll][Ee][Cc][Tt]
                  ( ~[;'] | '\'' ( ~['\\] | '\\' . )* '\'' )* ';' ;

// Keywords (case-insensitive)
LOAD            : [Ll][Oo][Aa][Dd] ;
FROM            : [Ff][Rr][Oo][Mm] ;
RESIDENT        : [Rr][Ee][Ss][Ii][Dd][Ee][Nn][Tt] ;
INLINE          : [Ii][Nn][Ll][Ii][Nn][Ee] ;
AUTOGENERATE    : [Aa][Uu][Tt][Oo][Gg][Ee][Nn][Ee][Rr][Aa][Tt][Ee] ;
WHERE           : [Ww][Hh][Ee][Rr][Ee] ;
GROUP           : [Gg][Rr][Oo][Uu][Pp] ;
ORDER           : [Oo][Rr][Dd][Ee][Rr] ;
HAVING          : [Hh][Aa][Vv][Ii][Nn][Gg] ;
BY              : [Bb][Yy] ;
AS              : [Aa][Ss] ;
ODBC            : [Oo][Dd][Bb][Cc] ;
OLEDB           : [Oo][Ll][Ee][Dd][Bb] ;
LIB             : [Ll][Ii][Bb] ;
CONNECT         : [Cc][Oo][Nn][Nn][Ee][Cc][Tt] ;
TO              : [Tt][Oo] ;
LEFT            : [Ll][Ee][Ff][Tt] ;
RIGHT           : [Rr][Ii][Gg][Hh][Tt] ;
INNER           : [Ii][Nn][Nn][Ee][Rr] ;
OUTER           : [Oo][Uu][Tt][Ee][Rr] ;
FULL            : [Ff][Uu][Ll][Ll] ;
JOIN            : [Jj][Oo][Ii][Nn] ;
KEEP            : [Kk][Ee][Ee][Pp] ;
CONCATENATE     : [Cc][Oo][Nn][Cc][Aa][Tt][Ee][Nn][Aa][Tt][Ee] ;
NOCONCATENATE   : [Nn][Oo][Cc][Oo][Nn][Cc][Aa][Tt][Ee][Nn][Aa][Tt][Ee] ;
MAPPING         : [Mm][Aa][Pp][Pp][Ii][Nn][Gg] ;
SET             : [Ss][Ee][Tt] ;
LET             : [Ll][Ee][Tt] ;
SUB             : [Ss][Uu][Bb] ;
END_SUB         : [Ee][Nn][Dd] [ \t]+ [Ss][Uu][Bb] ;
CALL            : [Cc][Aa][Ll][Ll] ;
DROP            : [Dd][Rr][Oo][Pp] ;
TABLE           : [Tt][Aa][Bb][Ll][Ee] ;
FIELD           : [Ff][Ii][Ee][Ll][Dd] ;
DISTINCT        : [Dd][Ii][Ss][Tt][Ii][Nn][Cc][Tt] ;
INCLUDE         : [Ii][Nn][Cc][Ll][Uu][Dd][Ee] ;
TRACE           : [Tt][Rr][Aa][Cc][Ee] ;
// v0.2 — additions for the v2 plan's Phase 1 grammar widening.
// BINARY load: inherits the full data model of another QVW.
BINARY          : [Bb][Ii][Nn][Aa][Rr][Yy] ;
// STORE … INTO 'path' (qvd|csv|txt): the missing producer half of the
// QVW-script lineage chain. Currently falls through to unknownStmt.
STORE           : [Ss][Tt][Oo][Rr][Ee] ;
INTO            : [Ii][Nn][Tt][Oo] ;
// QUALIFY / UNQUALIFY mutate field-name scoping for subsequent LOADs.
QUALIFY         : [Qq][Uu][Aa][Ll][Ii][Ff][Yy] ;
UNQUALIFY       : [Uu][Nn][Qq][Uu][Aa][Ll][Ii][Ff][Yy] ;
// SECTION ACCESS / SECTION APPLICATION — governance scoping.
SECTION         : [Ss][Ee][Cc][Tt][Ii][Oo][Nn] ;
ACCESS          : [Aa][Cc][Cc][Ee][Ss][Ss] ;
APPLICATION     : [Aa][Pp][Pp][Ll][Ii][Cc][Aa][Tt][Ii][Oo][Nn] ;
// RENAME TABLE old TO new / RENAME FIELD …
RENAME          : [Rr][Ee][Nn][Aa][Mm][Ee] ;

// Punctuation
COLON           : ':' ;
SEMI            : ';' ;
COMMA           : ',' ;
LPAREN          : '(' ;
RPAREN          : ')' ;
LBRACK          : '[' ;
RBRACK          : ']' ;
DOT             : '.' ;
EQUALS          : '=' ;
STAR            : '*' ;
PLUS            : '+' ;
MINUS           : '-' ;
SLASH           : '/' ;
PERCENT         : '%' ;
LT              : '<' ;
GT              : '>' ;
AMP             : '&' ;
BANG            : '!' ;

// Literals
STRING          : '\'' ( ~['\\] | '\\' . )* '\'' ;
NUMBER          : [0-9]+ ( '.' [0-9]+ )? ;

// Bracketed identifier (handles spaces / dots inside names: [Some Field])
BRACKETED       : '[' ~[\]\r\n]+ ']' ;

// Generic identifier (must come AFTER keywords)
ID              : [A-Za-z_] [A-Za-z_0-9]* ;

// Catch-all so the lexer never throws on stray characters in expressions.
OTHER           : . ;

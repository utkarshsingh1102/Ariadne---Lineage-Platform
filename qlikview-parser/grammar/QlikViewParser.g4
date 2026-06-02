parser grammar QlikViewParser;

options { tokenVocab = QlikViewLexer; }

// ---------------------------------------------------------------------------
// QlikView load-script parser (top-level structure).
//
// Expressions inside LOAD field lists are NOT parsed structurally — we
// collect their tokens and pass the raw text to a downstream expression-field
// extractor. The grammar only needs to know where statements start and end.
// ---------------------------------------------------------------------------

script
    : statement* EOF
    ;

statement
    : connectStmt
    | binaryStmt
    | storeStmt
    | loadStmt
    | sqlStmt
    | joinStmt
    | concatStmt
    | setStmt
    | letStmt
    | subStmt
    | callStmt
    | dropStmt
    | renameStmt
    | qualifyStmt
    | sectionStmt
    | traceStmt
    | includeStmt
    | unknownStmt
    ;

// ---------- CONNECT TO ----------
connectStmt
    : connectKind CONNECT TO connectionTarget SEMI?
    ;

connectKind
    : ODBC | OLEDB | LIB
    ;

connectionTarget
    : STRING
    | LBRACK connectionBody RBRACK
    | BRACKETED
    ;

connectionBody
    : ~RBRACK*
    ;

// ---------- LOAD ----------
loadStmt
    : tableLabel? mappingFlag? LOAD loadBody SEMI?
    ;

tableLabel
    : ( ID | BRACKETED ) COLON
    ;

mappingFlag
    : MAPPING
    ;

// LOAD body: arbitrary content up to the SEMI that closes the LOAD.
// We collect tokens here and re-tokenise in the visitor for clauses.
loadBody
    : loadToken*
    ;

loadToken
    : ~SEMI
    ;

// ---------- SQL block ----------
// The lexer captures the whole "SQL SELECT ... ;" as one token.
sqlStmt
    : SQL_BLOCK
    ;

// ---------- JOIN / KEEP / CONCATENATE ----------
joinStmt
    : joinPrefix JOIN joinTarget? mappingFlag? LOAD loadBody SEMI?
    | keepPrefix KEEP joinTarget? LOAD loadBody SEMI?
    ;

joinPrefix
    : LEFT
    | RIGHT
    | INNER
    | OUTER
    | FULL
    | LEFT OUTER
    | RIGHT OUTER
    | FULL OUTER
    ;

keepPrefix
    : LEFT
    | RIGHT
    | INNER
    ;

joinTarget
    : LPAREN ID RPAREN
    ;

concatStmt
    : NOCONCATENATE? CONCATENATE joinTarget? LOAD loadBody SEMI?
    | NOCONCATENATE
    ;

// ---------- SET / LET ----------
setStmt
    : SET ID EQUALS setValue SEMI?
    ;

letStmt
    : LET ID EQUALS setValue SEMI?
    ;

setValue
    : ~SEMI*
    ;

// ---------- SUB / END SUB / CALL ----------
subStmt
    : SUB ID ( LPAREN subParams? RPAREN )? subBody END_SUB SEMI?
    ;

subParams
    : ID ( COMMA ID )*
    ;

subBody
    : ( statement | subBodyToken )*?
    ;

subBodyToken
    : ~( END_SUB )
    ;

callStmt
    : CALL ID ( LPAREN callArgs? RPAREN )? SEMI?
    ;

callArgs
    : callArg ( COMMA callArg )*
    ;

callArg
    : STRING
    | ID
    | NUMBER
    ;

// ---------- DROP TABLE / DROP FIELD ----------
dropStmt
    : DROP ( TABLE | FIELD ) dropTargets ( FROM ID )? SEMI?
    ;

dropTargets
    : ID ( COMMA ID )*
    ;

// ---------- BINARY (v0.2) ----------
// BINARY '<path/to/upstream.qvw>'; — inherits the upstream app's data model.
binaryStmt
    : BINARY binaryTarget SEMI?
    ;

binaryTarget
    : STRING
    | BRACKETED
    ;

// ---------- STORE ... INTO (v0.2) ----------
// STORE [DISTINCT] <tableExpr> INTO '<path>' (qvd|csv|txt);
storeStmt
    : STORE DISTINCT? storeSource INTO storeTarget SEMI?
    ;

storeSource
    : ( ID | BRACKETED ) ( STAR | NUMBER | ID )*
    ;

storeTarget
    : STRING ( LPAREN storeFormat RPAREN )?
    | BRACKETED ( LPAREN storeFormat RPAREN )?
    ;

storeFormat
    : ID
    ;

// ---------- RENAME (v0.2) ----------
// RENAME TABLE old TO new ;     /     RENAME FIELD old TO new ;
renameStmt
    : RENAME ( TABLE | FIELD ) ( ID | BRACKETED ) TO ( ID | BRACKETED ) SEMI?
    ;

// ---------- QUALIFY / UNQUALIFY (v0.2) ----------
// Set parser-state flags on subsequent LOADs.
qualifyStmt
    : ( QUALIFY | UNQUALIFY ) qualifyFields SEMI?
    ;

qualifyFields
    : STAR
    | ( ID | BRACKETED ) ( COMMA ( ID | BRACKETED ) )*
    ;

// ---------- SECTION ACCESS / APPLICATION (v0.2) ----------
sectionStmt
    : SECTION ( ACCESS | APPLICATION ) SEMI?
    ;

// ---------- TRACE ----------
traceStmt
    : TRACE ~SEMI* SEMI?
    ;

// ---------- INCLUDE (legacy form) ----------
includeStmt
    : INCLUDE STRING SEMI?
    ;

// Catch-all so the parser never aborts on unfamiliar tokens.
unknownStmt
    : unknownToken+ SEMI?
    ;

unknownToken
    : ~( SEMI | LOAD | SQL_BLOCK | ODBC | OLEDB | LIB | SET | LET | SUB | CALL
       | DROP | TRACE | INCLUDE | CONCATENATE | NOCONCATENATE
       | BINARY | STORE | RENAME | QUALIFY | UNQUALIFY | SECTION )
    ;

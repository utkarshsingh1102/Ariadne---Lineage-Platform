// =============================================================================
// Neo4j init — applied via the neo4j-init one-shot service in docker-compose.
// The actual constraints live in lineage-contracts/schema/neo4j-constraints.cypher
// which is mounted directly and loaded by `cypher-shell -f`. This file exists
// so a human can run `cypher-shell -f init.cypher` against an empty DB if needed.
// =============================================================================

// Sanity:
RETURN "lineage-platform init script reachable" AS status;

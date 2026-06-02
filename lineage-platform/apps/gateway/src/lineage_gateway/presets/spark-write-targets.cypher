// Every Spark script, the tables it writes to (via :WRITES_TABLE), and the
// :Connection node owning each target. Returning both the table edge and the
// direct :DataFrame->:Connection edge lets the lineage UI render the sink
// connection inline next to the writing DataFrame.
MATCH path = (s:SparkScript)-[:CONTAINS_DATAFRAME]->(d:DataFrame)-[:WRITES_TABLE]->(t:Table)
OPTIONAL MATCH conn_path = (d)-[:WRITES_TO_CONNECTION]->(:Connection)
RETURN path, conn_path
LIMIT 500

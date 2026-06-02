// All :Connection nodes used by every Spark script, in both directions.
// Reads flow Connection -> DataFrame; writes flow DataFrame -> Connection.
// The frontend renders this as connection-on-the-left for sources and
// connection-on-the-right for sinks.
MATCH (s:SparkScript)-[:CONTAINS_DATAFRAME]->(d:DataFrame)
OPTIONAL MATCH src_path = (c_src:Connection)-[:PROVIDES_DATAFRAME]->(d)
OPTIONAL MATCH dst_path = (d)-[:WRITES_TO_CONNECTION]->(c_dst:Connection)
RETURN s, d, src_path, dst_path
LIMIT 500

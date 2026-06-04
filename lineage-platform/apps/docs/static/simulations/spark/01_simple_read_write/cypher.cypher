// SparkScript -> Cypher sketch
MERGE (s:SparkScript {id: $id})
  SET s.name = $name, s.file_path = $file_path, s.script_type = $script_type

// One MERGE per DataFrame in the chain:
MERGE (df:DataFrame {id: $df_id})
  SET df.label = $label
MERGE (s)-[:CONTAINS_DATAFRAME]->(df)

// Plus READS_TABLE / WRITES_TABLE / DERIVES_FROM_DATAFRAME edges —
// see spark-parser/graph/queries.py.
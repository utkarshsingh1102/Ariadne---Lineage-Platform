// Every physical :Table referenced by a Tableau workbook, with its datasource hop.
MATCH path = (w:TableauWorkbook)-[:CONTAINS_DATASOURCE]->(ds:TableauDatasource)-[:READS_TABLE]->(t:Table)
RETURN path
LIMIT 500

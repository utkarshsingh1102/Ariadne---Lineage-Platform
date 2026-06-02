"""Label + relationship-type constants for type-safety in callers."""

# Node labels (Tableau-owned)
TABLEAU_WORKBOOK = "TableauWorkbook"
TABLEAU_DATASOURCE = "TableauDatasource"
TABLEAU_WORKSHEET = "TableauWorksheet"
TABLEAU_DASHBOARD = "TableauDashboard"
PARAMETER = "Parameter"

# Node labels (shared — defined in lineage-contracts)
TABLE = "Table"
ATTRIBUTE = "Attribute"
CONNECTION = "Connection"

# Relationship types
CONTAINS_DATASOURCE = "CONTAINS_DATASOURCE"
CONNECTS_VIA = "CONNECTS_VIA"
READS_TABLE = "READS_TABLE"
HAS_FIELD = "HAS_FIELD"
HAS_COLUMN = "HAS_COLUMN"
DERIVES_FROM = "DERIVES_FROM"
CONTAINS_WORKSHEET = "CONTAINS_WORKSHEET"
CONTAINS_DASHBOARD = "CONTAINS_DASHBOARD"
USES_FIELD = "USES_FIELD"
DISPLAYS_WORKSHEET = "DISPLAYS_WORKSHEET"
HAS_PARAMETER = "HAS_PARAMETER"

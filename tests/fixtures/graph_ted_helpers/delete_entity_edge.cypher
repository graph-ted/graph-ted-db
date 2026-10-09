// Delete a single relationship by Graphiti edge uuid or Neo4j element id.
// Parameters: $uuid, $group_id
MATCH ()-[r]-()
WHERE (r.uuid = $uuid OR elementId(r) = $uuid)
  AND coalesce(r.group_id, $group_id) = $group_id
WITH r, coalesce(r.uuid, elementId(r)) AS deleted_uuid, coalesce(r.name, type(r)) AS deleted_name
DELETE r
RETURN deleted_uuid AS uuid,
       deleted_name AS name,
       true AS deleted

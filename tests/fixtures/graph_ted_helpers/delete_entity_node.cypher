// Delete an entity node (non-episodic) and all connected relationships.
// Parameters: $uuid, $group_id
MATCH (n:Entity {uuid: $uuid, group_id: $group_id})
WHERE NOT n:Episodic
WITH n, n.uuid AS deleted_uuid, n.name AS deleted_name
DETACH DELETE n
RETURN deleted_uuid AS uuid,
       deleted_name AS name,
       'entity' AS kind

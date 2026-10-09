// Delete an episodic node and all connected relationships.
// Parameters: $uuid, $group_id
MATCH (n:Episodic {uuid: $uuid, group_id: $group_id})
WITH n, n.uuid AS deleted_uuid, n.name AS deleted_name
DETACH DELETE n
RETURN deleted_uuid AS uuid,
       deleted_name AS name,
       'episode' AS kind

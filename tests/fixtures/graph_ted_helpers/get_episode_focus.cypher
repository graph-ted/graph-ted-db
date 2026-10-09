// Focus details for an episodic node (no embeddings).
// Parameters: $uuid, $group_id
MATCH (ep:Episodic {uuid: $uuid, group_id: $group_id})
RETURN ep.uuid AS uuid,
       ep.name AS name,
       ep.content AS content,
       ep.created_at AS created_at,
       ep.source_description AS source_description,
       properties(ep) AS properties
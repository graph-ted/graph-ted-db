// Focus details for an entity node (no embeddings).
// Parameters: $uuid, $group_id
MATCH (n:Entity {uuid: $uuid, group_id: $group_id})
WHERE NOT n:Episodic
RETURN n.uuid AS uuid,
       n.name AS name,
       [label IN labels(n) WHERE label <> 'Entity'] AS entity_types,
       n.summary AS summary,
       n.created_at AS created_at,
       n.attributes AS attributes,
       properties(n) AS properties
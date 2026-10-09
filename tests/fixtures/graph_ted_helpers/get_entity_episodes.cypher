// Episodes mentioning a focus entity, with co-mentioned entities for graph links.
// Parameters: $uuid, $group_id
MATCH (focus:Entity {uuid: $uuid, group_id: $group_id})
MATCH (ep:Episodic {group_id: $group_id})-[:MENTIONS]->(focus)
OPTIONAL MATCH (ep)-[:MENTIONS]->(other:Entity {group_id: $group_id})
WHERE other <> focus
WITH ep, focus.uuid AS focus_uuid, collect(DISTINCT other.uuid) AS other_entity_uuids
RETURN ep.uuid AS uuid,
       ep.name AS name,
       ep.content AS content,
       ep.created_at AS created_at,
       [focus_uuid] + other_entity_uuids AS mentioned_entity_uuids,
       size(other_entity_uuids) = 0 AS exclusive
ORDER BY ep.created_at DESC
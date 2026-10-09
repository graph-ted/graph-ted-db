// Other episodes that mention any entity co-mentioned by the focus episode.
// Parameters: $uuid, $group_id
MATCH (focus_ep:Episodic {uuid: $uuid, group_id: $group_id})-[:MENTIONS]->(ent:Entity {group_id: $group_id})
MATCH (ep:Episodic {group_id: $group_id})-[:MENTIONS]->(ent)
WHERE ep <> focus_ep
OPTIONAL MATCH (ep)-[:MENTIONS]->(other:Entity {group_id: $group_id})
WITH ep, collect(DISTINCT other.uuid) AS mentioned_entity_uuids
RETURN ep.uuid AS uuid,
       ep.name AS name,
       ep.content AS content,
       ep.created_at AS created_at,
       mentioned_entity_uuids,
       false AS exclusive
ORDER BY ep.created_at DESC
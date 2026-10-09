// Question entities mentioned by a focus episode.
// Parameters: $uuid, $group_id
MATCH (ep:Episodic {uuid: $uuid, group_id: $group_id})-[:MENTIONS]->(question:Entity {group_id: $group_id})
WHERE 'Question' IN labels(question)
RETURN DISTINCT question.uuid AS uuid,
       question.name AS name,
       [label IN labels(question) WHERE label <> 'Entity'] AS entity_types,
       'MENTIONS' AS relationship_type,
       '' AS relationship_fact
ORDER BY toLower(question.name)
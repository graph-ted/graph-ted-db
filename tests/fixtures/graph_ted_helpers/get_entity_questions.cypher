// Question entities related to a focus entity.
// Parameters: $uuid, $group_id
MATCH (focus:Entity {uuid: $uuid, group_id: $group_id})
MATCH (focus)-[r]-(question:Entity {group_id: $group_id})
WHERE 'Question' IN labels(question)
RETURN DISTINCT question.uuid AS uuid,
       question.name AS name,
       [label IN labels(question) WHERE label <> 'Entity'] AS entity_types,
       type(r) AS relationship_type,
       coalesce(r.fact, r.name, '') AS relationship_fact
ORDER BY toLower(question.name)
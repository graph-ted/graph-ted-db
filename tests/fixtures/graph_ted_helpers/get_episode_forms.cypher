// Form / QuestionList entities mentioned by a focus episode.
// Parameters: $uuid, $group_id
MATCH (ep:Episodic {uuid: $uuid, group_id: $group_id})-[:MENTIONS]->(form:Entity {group_id: $group_id})
WHERE 'Form' IN labels(form) OR 'QuestionList' IN labels(form)
RETURN DISTINCT form.uuid AS uuid,
       form.name AS name,
       [label IN labels(form) WHERE label <> 'Entity'] AS entity_types,
       form.attributes AS attributes,
       properties(form) AS properties
ORDER BY toLower(form.name)

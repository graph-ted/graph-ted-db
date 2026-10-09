// Current (temporally valid) neighbor edges for one or more target entities.
// Callers filter is_answer (+ question_key for forms) in the app layer because
// Graphiti may store attributes as a JSON string or nested map.
// Parameters: $uuids (list of entity uuids), $group_id
UNWIND $uuids AS target_uuid
MATCH (target:Entity {uuid: target_uuid, group_id: $group_id})
MATCH (answerer:Entity {group_id: $group_id})-[e]-(target)
WHERE answerer <> target
  AND NOT answerer:Episodic
  AND (e.invalid_at IS NULL OR e.invalid_at = '')
RETURN target.uuid AS target_uuid,
       answerer.uuid AS uuid,
       answerer.name AS name,
       [label IN labels(answerer) WHERE label <> 'Entity'] AS entity_types,
       type(e) AS relationship_type,
       e.name AS edge_name,
       e.fact AS fact,
       e.valid_at AS valid_at,
       e.attributes AS attributes,
       properties(e) AS edge_properties,
       answerer.attributes AS answerer_attributes,
       properties(answerer) AS answerer_properties
ORDER BY target_uuid, e.valid_at DESC, toLower(answerer.name)

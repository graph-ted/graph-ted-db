// Non-question entities mentioned by a focus episode (one row per MENTIONS edge).
// Forms nest under parent tiles; HAS_FORM Form neighbors appear as native tiles.
// Most recent knowledge first (valid_at, else edge created_at, else related created_at).
// Parameters: $uuid, $group_id
MATCH (ep:Episodic {uuid: $uuid, group_id: $group_id})-[r:MENTIONS]->(related:Entity {group_id: $group_id})
WHERE NOT 'Question' IN labels(related)
  AND (
    (
      NOT 'Form' IN labels(related)
      AND NOT 'QuestionList' IN labels(related)
    )
    OR coalesce(r.name, '') = 'HAS_FORM'
  )
OPTIONAL MATCH (related)-[fr]-(form:Entity {group_id: $group_id})
WHERE form <> related
  AND ('Form' IN labels(form) OR 'QuestionList' IN labels(form))
WITH related, r,
     collect(DISTINCT CASE
       WHEN form IS NULL THEN NULL
       ELSE {
         uuid: form.uuid,
         name: form.name,
         entity_types: [label IN labels(form) WHERE label <> 'Entity'],
         attributes: form.attributes
       }
     END) AS form_maps
RETURN related.uuid AS uuid,
       related.name AS name,
       [label IN labels(related) WHERE label <> 'Entity'] AS entity_types,
       related.attributes AS attributes,
       coalesce(r.uuid, elementId(r)) AS edge_uuid,
       type(r) AS relationship_type,
       coalesce(r.name, 'MENTIONS') AS edge_name,
       coalesce(r.fact, '') AS relationship_fact,
       r.valid_at AS valid_at,
       r.invalid_at AS invalid_at,
       r.created_at AS edge_created_at,
       related.created_at AS related_created_at,
       [f IN form_maps WHERE f IS NOT NULL] AS forms
ORDER BY toString(coalesce(r.valid_at, r.created_at, related.created_at, '')) DESC,
         toLower(related.name)

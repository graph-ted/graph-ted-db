// Form / QuestionList entities associated with a focus entity.
// Includes the focus itself when it is a Form, plus neighboring Forms
// (parent HAS_FORM / RELATES_TO hard edge). Used by the Questions panel.
// Parameters: $uuid, $group_id
MATCH (focus:Entity {uuid: $uuid, group_id: $group_id})
OPTIONAL MATCH (focus)-[r]-(neighbor:Entity {group_id: $group_id})
WHERE neighbor <> focus
  AND NOT neighbor:Episodic
  AND ('Form' IN labels(neighbor) OR 'QuestionList' IN labels(neighbor))
WITH focus, collect(DISTINCT neighbor) AS neighbors
WITH CASE
       WHEN 'Form' IN labels(focus) OR 'QuestionList' IN labels(focus)
         THEN neighbors + focus
       ELSE neighbors
     END AS forms
UNWIND forms AS form
WITH DISTINCT form
WHERE form IS NOT NULL
RETURN form.uuid AS uuid,
       form.name AS name,
       [label IN labels(form) WHERE label <> 'Entity'] AS entity_types,
       form.attributes AS attributes,
       properties(form) AS properties
ORDER BY toLower(form.name)

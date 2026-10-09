// List entity nodes (not episodes) in a Graphiti data group.
// Form / QuestionList instances are nested under their parent, not listed as tiles.
// Most recent knowledge first: latest edge valid_at/created_at, else node created_at.
// Parameters: $group_id (e.g. "default")
MATCH (n:Entity {group_id: $group_id})
WHERE NOT n:Episodic
  AND NOT n:EntityType
  AND NOT n:RelationshipType
  AND NOT n:Form
  AND NOT n:QuestionList
OPTIONAL MATCH (n)-[r]-()
WITH n,
     [label IN labels(n) WHERE label <> 'Entity'] AS entity_types,
     max(coalesce(r.valid_at, r.created_at)) AS last_edge_at
OPTIONAL MATCH (n)-[fr]-(form:Entity {group_id: $group_id})
WHERE form <> n
  AND ('Form' IN labels(form) OR 'QuestionList' IN labels(form))
WITH n, entity_types, last_edge_at,
     collect(DISTINCT CASE
       WHEN form IS NULL THEN NULL
       ELSE {
         uuid: form.uuid,
         name: form.name,
         entity_types: [label IN labels(form) WHERE label <> 'Entity'],
         attributes: form.attributes
       }
     END) AS form_maps
RETURN n.uuid AS uuid,
       n.name AS name,
       entity_types AS entity_types,
       [f IN form_maps WHERE f IS NOT NULL] AS forms
ORDER BY toString(coalesce(last_edge_at, n.created_at, '')) DESC,
         toLower(n.name)

// Entities in the 1-hop neighborhood of a knowledge-focus node.
// Soft UI filter only — not Graphiti hard isolation.
// Form / QuestionList instances are nested under their parent, not listed as tiles.
// Most recent knowledge first: latest edge valid_at/created_at, else node created_at.
// Parameters: $group_id, $focus_uuid
//
// Focus may be an Entity or Episodic node:
// - Entity: seed = focus; include seed + direct entity neighbors
// - Episode: seed = mentioned entities; include seeds + their direct neighbors

MATCH (focus {uuid: $focus_uuid, group_id: $group_id})
WHERE focus:Entity OR focus:Episodic

OPTIONAL MATCH (focus)-[:MENTIONS]->(mentioned:Entity {group_id: $group_id})
WHERE NOT mentioned:Episodic
  AND NOT mentioned:EntityType
  AND NOT mentioned:RelationshipType

WITH focus,
     collect(DISTINCT mentioned) AS mentioned_entities,
     CASE
       WHEN focus:Episodic THEN []
       WHEN focus:Entity AND NOT focus:Episodic THEN [focus]
       ELSE []
     END AS focus_as_entity

WITH [n IN (focus_as_entity + mentioned_entities) WHERE n IS NOT NULL] AS seeds
WHERE size(seeds) > 0

UNWIND seeds AS seed
OPTIONAL MATCH (seed)-[r]-(related:Entity {group_id: $group_id})
WHERE related <> seed
  AND NOT related:Episodic
  AND NOT related:EntityType
  AND NOT related:RelationshipType

WITH collect(DISTINCT seed) AS seed_nodes, collect(DISTINCT related) AS related_nodes
WITH [n IN (seed_nodes + related_nodes) WHERE n IS NOT NULL] AS all_nodes
UNWIND all_nodes AS n
WITH DISTINCT n
WHERE n:Entity
  AND NOT n:Episodic
  AND NOT n:EntityType
  AND NOT n:RelationshipType
  AND NOT n:Form
  AND NOT n:QuestionList

OPTIONAL MATCH (n)-[edge]-()
WITH n,
     [label IN labels(n) WHERE label <> 'Entity'] AS entity_types,
     max(coalesce(edge.valid_at, edge.created_at)) AS last_edge_at
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

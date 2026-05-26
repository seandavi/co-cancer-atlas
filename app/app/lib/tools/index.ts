// Tool registry handed to streamText(). Add new tools by exporting
// them here. Each tool is `tool({...})` from the AI SDK so the model
// gets schema + execute together.

import { listMeasures } from "./listMeasures";
import { describeMeasure } from "./describeMeasure";
import { queryData } from "./queryData";
import { plot } from "./plot";
import { renderChoropleth } from "./renderChoropleth";

export const atlasTools = {
  list_measures: listMeasures,
  describe_measure: describeMeasure,
  query_data: queryData,
  plot,
  render_choropleth: renderChoropleth,
};

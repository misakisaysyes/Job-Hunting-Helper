import { FIELD_HELP } from "./configFields";
import ChoiceMultiSelect from "./ChoiceMultiSelect";

export default function CityMultiSelect({ options, values, onChange }) {
  return <ChoiceMultiSelect id="config-collection-cities" label="城市" path="collection.cities"
    options={options.map((city) => [city, city])} values={values} onChange={onChange}
    emptyText="选择城市（不限）" help={FIELD_HELP["collection.cities"]} />;
}

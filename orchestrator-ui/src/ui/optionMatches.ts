import type { ComboOption } from './ComboBox';

/** Whether `option` matches the typed `needle`, already lowercased. */
export function optionMatches(option: ComboOption, needle: string): boolean {
  return [option.value, option.display ?? '', option.label ?? ''].some((field) =>
    field.toLowerCase().includes(needle),
  );
}

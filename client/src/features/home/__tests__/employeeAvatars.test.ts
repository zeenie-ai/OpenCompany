import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { getDefaultEmployeeAvatar } from '../data/avatars';

const portraitNames = ['builder', 'grower', 'chief-of-staff', 'front-desk', 'researcher', 'treasurer'];
const assetDirectory = join(__dirname, '..', '..', '..', 'assets', 'employee-avatars');

describe('default employee portraits', () => {
  it('keeps an employee assigned to the same portrait independently of other requests', () => {
    const initial = getDefaultEmployeeAvatar('stable-workflow-id');
    for (let index = 0; index < 30; index++) getDefaultEmployeeAvatar(`another-${index}`);
    expect(getDefaultEmployeeAvatar('stable-workflow-id')).toBe(initial);
  });

  it.each([
    ['employee-1', 'grower'],
    ['employee-2', 'builder'],
    ['legacy-workflow', 'builder'],
    ['01a1aab0-da08-7111-be44-df13e9b7d4c5', 'treasurer'],
    ['12345678901234567890', 'treasurer'],
    ['研究者', 'researcher'],
  ])('preserves the assigned portrait for %s', (workflowId, portrait) => {
    expect(getDefaultEmployeeAvatar(workflowId)).toMatch(new RegExp(`/employee-avatars/${portrait}\\.png$`));
  });

  it('draws from all six bundled portraits for different employees', () => {
    const assigned = new Set(Array.from({ length: 300 }, (_, index) => getDefaultEmployeeAvatar(`employee-${index}`)));
    expect(assigned.size).toBe(portraitNames.length);
    for (const name of portraitNames) {
      expect([...assigned].some((url) => url.endsWith(`/employee-avatars/${name}.png`))).toBe(true);
    }
  });

  it.each(portraitNames)('ships the %s portrait as a lightweight 256px square PNG', (name) => {
    const image = readFileSync(join(assetDirectory, `${name}.png`));
    expect(image.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    expect(image.toString('ascii', 12, 16)).toBe('IHDR');
    expect(image.readUInt32BE(16)).toBe(256);
    expect(image.readUInt32BE(20)).toBe(256);
  });
});

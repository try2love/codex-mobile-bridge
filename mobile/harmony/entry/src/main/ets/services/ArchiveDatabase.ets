import { common } from '@kit.AbilityKit';
import { relationalStore } from '@kit.ArkData';

export type ArchiveValue = string | number | Uint8Array | null;
export interface ArchiveDatabase {
  run(sql: string, values?: ArchiveValue[]): Promise<void>;
  rows(sql: string, values?: ArchiveValue[]): Promise<ArchiveValue[][]>;
}

export class RdbArchiveDatabase implements ArchiveDatabase {
  private store: relationalStore.RdbStore | undefined = undefined;
  async init(context: common.Context): Promise<void> {
    this.store = await relationalStore.getRdbStore(context, {
      name: 'offline-archive.db', securityLevel: relationalStore.SecurityLevel.S3, encrypt: true
    });
  }
  async run(sql: string, values: ArchiveValue[] = []): Promise<void> {
    if (!this.store) { throw new Error('离线存储尚未就绪'); }
    if (sql === 'BEGIN') { this.store.beginTransaction(); }
    else if (sql === 'COMMIT') { this.store.commit(); }
    else if (sql === 'ROLLBACK') { this.store.rollBack(); }
    else { await this.store.executeSql(sql, values); }
  }
  async rows(sql: string, values: ArchiveValue[] = []): Promise<ArchiveValue[][]> {
    if (!this.store) { throw new Error('离线存储尚未就绪'); }
    const result = await this.store.querySql(sql, values);
    const rows: ArchiveValue[][] = [];
    try {
      while (result.goToNextRow()) {
        const row: ArchiveValue[] = [];
        for (let index = 0; index < result.columnCount; index++) { row.push(result.getValue(index) as ArchiveValue); }
        rows.push(row);
      }
    } finally { result.close(); }
    return rows;
  }
}

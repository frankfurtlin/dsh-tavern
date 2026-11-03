import {it,expect} from 'vitest'
import {WritingSecretFilter} from './secret-filter'
it('refuses a split credential before publishing any part of its matching prefix',()=>{const filter=new WritingSecretFilter('OWN_SECRET_KEY');expect(filter.feed('正文。OWN_')).toBe('正文。');expect(filter.feed('SECRET_')).toBe('');expect(()=>filter.feed('KEY')).toThrow();expect(()=>filter.check('header: OWN_SECRET_KEY')).toThrow()})
it('releases an ordinary coincidental prefix once disambiguated or normally finished',()=>{const filter=new WritingSecretFilter('abab-secret');expect(filter.feed('句末ab')).toBe('句末');expect(filter.feed('ac。')).toBe('abac。');expect(filter.feed('尾ab')).toBe('尾');expect(filter.finish()).toBe('ab')})
it('keeps empty local credentials transparent',()=>{const filter=new WritingSecretFilter('');expect(filter.feed('甲😺乙')).toBe('甲😺乙');expect(filter.finish()).toBe('')})
it('matches a UTF16 credential even when a surrogate pair is split between packets',()=>{const filter=new WritingSecretFilter('OWN😺KEY');expect(filter.feed('正文OWN\ud83d')).toBe('正文');expect(filter.feed('\ude3aKE')).toBe('');expect(()=>filter.feed('Y')).toThrow()})

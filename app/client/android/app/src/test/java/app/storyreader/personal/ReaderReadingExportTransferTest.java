package app.storyreader.personal;

import static org.junit.Assert.*;
import java.io.*;
import java.security.MessageDigest;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.Test;

/** Public synthetic inputs, no Android, server, credentials or real user files. */
public class ReaderReadingExportTransferTest {
    private static final String ID="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", OWNER="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private static final String ZERO=new String(new char[64]).replace('\0','0');
    private interface Checked {void run()throws Exception;}
    private static void invalid(Checked work)throws Exception{try{work.run();fail("expected rejection");}catch(ReaderBackupTransferPolicy.Failure expected){assertEquals("EXPORT_FILE_INVALID",expected.code);}}
    private static String hash(byte[] bytes)throws Exception{StringBuilder out=new StringBuilder();for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes))out.append(String.format("%02x",b&255));return out.toString();}
    private static ReaderBackupTransferPolicy.ReadingExport meta(String format,String name)throws Exception{return ReaderBackupTransferPolicy.readingExport(ID,format,123L,ZERO,name);}
    @Test public void publicMetadataKeepsCjkNameAndExactFixedPath()throws Exception{
        ReaderBackupTransferPolicy p=new ReaderBackupTransferPolicy();p.setContext("https://reader.example/",OWNER,0,false);
        assertEquals("https://reader.example/api/reader/v1/exports/"+ID+"/download",ReaderBackupTransferPolicy.readingEndpoint(p.current(),ID).toString());
        assertEquals("故事🙂.txt",meta("txt","故事🙂.txt").fileName);assertEquals("application/epub+zip",meta("epub","故事.epub").mime);
        invalid(()->ReaderBackupTransferPolicy.readingEndpoint(p.current(),"../download"));
    }
    @Test public void unsafeNamesAndInvalidUtf16NeverReachSystemPicker()throws Exception{
        for(String name:new String[]{".hidden.txt","../x.txt","a\\b.txt","x:secret.txt","x\n.txt","x\r.txt","x\u007f.txt","x.txt ","x.epub","a\ud800.txt","x.TXT","x\".txt","x?.txt"})invalid(()->meta("txt",name));
        invalid(()->meta("txt",new String(new char[180]).replace('\0','中')+".txt"));
    }
    @Test public void sizeMustBePositiveBoundedNumberAndDigestLowercaseHex()throws Exception{
        for(Object size:new Object[]{"123",true,0L,-1L,Double.NaN,Double.POSITIVE_INFINITY,1.5,ReaderBackupTransferPolicy.MAX_READING_BYTES+1})invalid(()->ReaderBackupTransferPolicy.readingExport(ID,"txt",size,ZERO,"a.txt"));
        assertEquals(2147483648L,ReaderBackupTransferPolicy.readingExport(ID,"txt",2147483648L,ZERO,"a.txt").size);
        invalid(()->ReaderBackupTransferPolicy.readingExport(ID,"txt",1L,"A"+ZERO.substring(1),"a.txt"));
    }
    @Test public void responseTypeAndLengthCannotBeReinterpretedAsBackupOrHtml()throws Exception{
        ReaderBackupTransferPolicy.ReadingExport m=meta("txt","a.txt");m.checkResponse("text/plain; charset=utf-8","identity",123);m.checkResponse("TEXT/PLAIN",null,-1);
        invalid(()->m.checkResponse("text/html",null,123));invalid(()->m.checkResponse("application/octet-stream",null,123));invalid(()->m.checkResponse("text/plain","gzip",123));invalid(()->m.checkResponse("text/plain",null,122));
    }
    @Test public void currentDocumentRequiresExact32HexNonce()throws Exception{
        assertTrue(ReaderBackupTransferPolicy.sameDocument(OWNER,OWNER));assertFalse(ReaderBackupTransferPolicy.sameDocument(ID,OWNER));
        assertFalse(ReaderBackupTransferPolicy.sameDocument(null,OWNER));assertFalse(ReaderBackupTransferPolicy.sameDocument(OWNER,42));assertFalse(ReaderBackupTransferPolicy.sameDocument("A"+OWNER.substring(1),"A"+OWNER.substring(1)));
    }
    @Test public void plainTextWithoutCipherHeaderIsCopiedAndVerified()throws Exception{
        byte[] text="公开故事🙂\n第二行".getBytes(StandardCharsets.UTF_8);ByteArrayOutputStream target=new ByteArrayOutputStream();
        assertEquals(text.length,ReaderMultipartStream.copyVerified(new ByteArrayInputStream(text),target,text.length,hash(text),()->{},bytes->{}));assertArrayEquals(text,target.toByteArray());
    }
    @Test public void digestAndShortLongStreamsCannotProduceVerifiedSuccess()throws Exception{
        byte[] text=new byte[]{1,2,3};invalid(()->ReaderMultipartStream.copyVerified(new ByteArrayInputStream(text),new ByteArrayOutputStream(),3,ZERO,()->{},bytes->{}));
        for(int length:new int[]{2,4})invalid(()->ReaderMultipartStream.copyVerified(new ByteArrayInputStream(text),new ByteArrayOutputStream(),length,hash(text),()->{},bytes->{}));
    }
    @Test public void zeroReadsProgressAndCancellationAfterReadDoesNotWriteOldBytes()throws Exception{
        byte[] text=new byte[]{1,2,3};InputStream zero=new ByteArrayInputStream(text){@Override public synchronized int read(byte[] b,int o,int n){return 0;}};
        assertEquals(3,ReaderMultipartStream.copyVerified(zero,new ByteArrayOutputStream(),3,hash(text),()->{},bytes->{}));
        ReaderBackupTransferPolicy p=new ReaderBackupTransferPolicy();p.setContext("https://reader.example",OWNER,1,false);ReaderBackupTransferPolicy.Ticket ticket=p.begin(1);
        InputStream cancel=new ByteArrayInputStream(text){@Override public synchronized int read(byte[] b,int o,int n){int count=super.read(b,o,n);p.cancel(ticket);return count;}};
        ByteArrayOutputStream out=new ByteArrayOutputStream();try{ReaderMultipartStream.copyVerified(cancel,out,3,hash(text),ticket::check,bytes->{});fail();}catch(ReaderBackupTransferPolicy.Failure e){assertEquals("BACKUP_CONTEXT_CHANGED",e.code);}assertEquals(0,out.size());
    }
    @Test public void multiChunkRealFileUsesBoundedReadsAndMonotoneProgress()throws Exception{
        File file=File.createTempFile("reader-public-owned-",".txt");byte[] block=new byte[65536];Arrays.fill(block,(byte)'a');MessageDigest digest=MessageDigest.getInstance("SHA-256");
        try{try(OutputStream out=new FileOutputStream(file)){for(int i=0;i<33;i++){out.write(block);digest.update(block);}}
            StringBuilder sha=new StringBuilder();for(byte b:digest.digest())sha.append(String.format("%02x",b&255));AtomicLong max=new AtomicLong(),last=new AtomicLong(),written=new AtomicLong();
            try(InputStream source=new FilterInputStream(new FileInputStream(file)){@Override public int read(byte[] b,int o,int n)throws IOException{max.accumulateAndGet(n,Math::max);return in.read(b,o,n);}}){
                OutputStream sink=new OutputStream(){public void write(int b){written.incrementAndGet();}public void write(byte[] b,int o,int n){written.addAndGet(n);}};
                ReaderMultipartStream.copyVerified(source,sink,file.length(),sha.toString(),()->{},bytes->{assertTrue(bytes>last.get());last.set(bytes);});
            }assertEquals(65536,max.get());assertEquals(file.length(),written.get());assertEquals(file.length(),last.get());
        }finally{assertTrue(file.delete());}
    }
}
